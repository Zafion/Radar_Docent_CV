from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from threading import Lock
from typing import Deque

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


@dataclass(frozen=True)
class RateLimitRule:
    max_requests: int
    window_seconds: int


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory rate limiting for public API routes.

    Intended for a single self-hosted instance behind Cloudflare or a reverse proxy.
    It limits only /api/* routes and trusts these headers in order:
    1) CF-Connecting-IP
    2) X-Forwarded-For
    3) request.client.host
    """

    def __init__(self, app) -> None:
        super().__init__(app)
        self.default_rule = RateLimitRule(
            max_requests=_env_int("RADAR_API_RATE_LIMIT_MAX_REQUESTS", 20),
            window_seconds=_env_int("RADAR_API_RATE_LIMIT_WINDOW_SECONDS", 10),
        )
        self.search_rule = RateLimitRule(
            max_requests=_env_int("RADAR_API_RATE_LIMIT_SEARCH_MAX_REQUESTS", 10),
            window_seconds=_env_int("RADAR_API_RATE_LIMIT_SEARCH_WINDOW_SECONDS", 10),
        )
        self._requests: dict[str, Deque[float]] = defaultdict(deque)
        self._lock = Lock()
        self._heavy_paths = {
            "/api/search/persons",
            "/api/persons/profile",
        }

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not path.startswith("/api/"):
            return await call_next(request)

        client_ip = self._extract_client_ip(request)
        rule = self._select_rule(path)
        bucket_key = f"{client_ip}:{path if path in self._heavy_paths else '*'}"

        allowed, retry_after, remaining = self._check_limit(bucket_key, rule)
        if not allowed:
            headers = {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(rule.max_requests),
                "X-RateLimit-Window": str(rule.window_seconds),
                "X-RateLimit-Remaining": "0",
            }
            return JSONResponse(
                status_code=429,
                content={"detail": "Too Many Requests"},
                headers=headers,
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(rule.max_requests)
        response.headers["X-RateLimit-Window"] = str(rule.window_seconds)
        response.headers["X-RateLimit-Remaining"] = str(max(remaining, 0))
        return response

    def _select_rule(self, path: str) -> RateLimitRule:
        if path in self._heavy_paths:
            return self.search_rule
        return self.default_rule

    def _extract_client_ip(self, request: Request) -> str:
        cf_ip = request.headers.get("CF-Connecting-IP", "").strip()
        if cf_ip:
            return cf_ip

        x_forwarded_for = request.headers.get("X-Forwarded-For", "").strip()
        if x_forwarded_for:
            return x_forwarded_for.split(",")[0].strip()

        if request.client and request.client.host:
            return request.client.host

        return "unknown"

    def _check_limit(self, key: str, rule: RateLimitRule) -> tuple[bool, int, int]:
        now = time.monotonic()
        window_start = now - rule.window_seconds

        with self._lock:
            queue = self._requests[key]

            while queue and queue[0] <= window_start:
                queue.popleft()

            if len(queue) >= rule.max_requests:
                retry_after = max(1, int(queue[0] + rule.window_seconds - now))
                return False, retry_after, 0

            queue.append(now)
            remaining = rule.max_requests - len(queue)
            return True, 0, remaining

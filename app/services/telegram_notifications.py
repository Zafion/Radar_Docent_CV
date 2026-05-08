from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

TELEGRAM_API_BASE = "https://api.telegram.org"


def get_telegram_bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def get_telegram_channel_id() -> str:
    return os.getenv("TELEGRAM_CHANNEL_ID", "").strip()


def is_telegram_configured() -> bool:
    return bool(get_telegram_bot_token() and get_telegram_channel_id())


def get_telegram_channel_url() -> str | None:
    configured = os.getenv("TELEGRAM_CHANNEL_URL", "").strip()
    if configured:
        return configured
    channel_id = get_telegram_channel_id()
    if channel_id.startswith("@"):
        return f"https://t.me/{channel_id[1:]}"
    return None


def send_telegram_message(text: str) -> dict[str, Any]:
    token = get_telegram_bot_token()
    chat_id = get_telegram_channel_id()
    if not token or not chat_id:
        raise RuntimeError("telegram_not_configured")

    disable_preview = os.getenv("TELEGRAM_DISABLE_WEB_PAGE_PREVIEW", "false").strip().lower() in {
        "1", "true", "yes", "on"
    }
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": disable_preview}
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{TELEGRAM_API_BASE}/bot{token}/sendMessage",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"telegram_http_{exc.code}: {error_body}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"telegram_url_error: {exc}") from exc

    result = json.loads(body)
    if not result.get("ok"):
        raise RuntimeError(f"telegram_api_error: {result}")
    return result

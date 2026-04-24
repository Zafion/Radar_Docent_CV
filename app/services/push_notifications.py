from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.storage.push_subscription_store import (
    delete_push_subscription,
    list_active_push_subscriptions,
)


def _read_env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _resolve_vapid_private_key(value: str) -> str:
    if not value:
        return ""

    candidate = Path(value).expanduser()
    if candidate.exists() and candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()

    return value


VAPID_PUBLIC_KEY = _read_env("RADAR_PUSH_VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = _resolve_vapid_private_key(_read_env("RADAR_PUSH_VAPID_PRIVATE_KEY"))
VAPID_SUBJECT = _read_env("RADAR_PUSH_VAPID_SUBJECT", "mailto:funkcionarios@gmail.com")


def get_vapid_public_key() -> str:
    return VAPID_PUBLIC_KEY


def is_push_configured() -> bool:
    return bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and VAPID_SUBJECT)


def send_push_notification_to_all(
    conn: Any,
    *,
    title: str,
    body: str,
    url: str,
) -> dict[str, Any]:
    if not is_push_configured():
        return {"sent": 0, "failed": 0, "deleted": 0, "reason": "push_not_configured"}

    from pywebpush import WebPushException, webpush

    payload = json.dumps(
        {
            "title": title,
            "body": body,
            "url": url,
        }
    )

    sent = 0
    failed = 0
    deleted = 0

    subscriptions = list_active_push_subscriptions(conn)

    for sub in subscriptions:
        subscription_info = {
            "endpoint": sub["endpoint"],
            "keys": {
                "p256dh": sub["p256dh_key"],
                "auth": sub["auth_key"],
            },
        }

        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={"sub": VAPID_SUBJECT},
            )
            sent += 1
        except WebPushException as exc:
            failed += 1
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            if status_code in (404, 410):
                delete_push_subscription(conn, sub["endpoint"])
                deleted += 1

    return {"sent": sent, "failed": failed, "deleted": deleted}

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from app.services.notification_policy import notifications_enabled
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
        # pywebpush accepts a PEM file path. Do not read PEM contents here:
        # passing the raw PEM text makes py_vapid try to decode it as DER/base64.
        return str(candidate)

    return value


VAPID_PUBLIC_KEY = _read_env("RADAR_PUSH_VAPID_PUBLIC_KEY")
VAPID_PRIVATE_KEY = _resolve_vapid_private_key(_read_env("RADAR_PUSH_VAPID_PRIVATE_KEY"))
VAPID_SUBJECT = _read_env("RADAR_PUSH_VAPID_SUBJECT", "mailto:zafion+funkcionario@gmail.com")


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
    if not notifications_enabled():
        return {"sent": 0, "failed": 0, "deleted": 0, "disabled": True, "reason": "notifications_disabled"}
    if not is_push_configured():
        return {"sent": 0, "failed": 0, "deleted": 0, "reason": "push_not_configured"}

    # Compatibility entrypoint for local/manual callers: detach recipients and
    # commit their read transaction BEFORE invoking the bounded transport.
    import time
    from app.services.notification_transport import prepare_push_recipients,send_push_snapshot,BATCH_SECONDS,dispatch_transport,deactivate_expired_recipients
    subscriptions,total=prepare_push_recipients(conn)
    result=send_push_snapshot(subscriptions,total,dict(title=title,body=body,url=url),
                              time.monotonic()+BATCH_SECONDS,dispatch=dispatch_transport)
    result['deactivated']=deactivate_expired_recipients(conn,subscriptions,result)
    conn.commit()
    result['deleted']=0
    return result

from __future__ import annotations

import json
from typing import Any
from app.storage.notification_eligibility_store import eligible_event_sql, deliverable_event_sql


def enqueue_push_notification_event(
    conn: Any,
    *,
    event_key: str,
    event_type: str,
    title: str,
    body: str,
    url: str,
    payload: dict[str, Any] | None = None,
) -> None:
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO push_notification_events (
            event_key,
            event_type,
            title,
            body,
            url,
            payload_json,
            status
        )
        VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'pending')
        ON CONFLICT (event_key) DO NOTHING
        """,
        (event_key, event_type, title, body, url, payload_json),
    )


def list_pending_push_notification_events(conn: Any, *, limit: int = 20) -> list[dict[str, Any]]:
    gate,gate_params=deliverable_event_sql("push_notification_events.event_key")
    rows = conn.execute(
        """
        SELECT
            id,
            event_key,
            event_type,
            title,
            body,
            url,
            payload_json,
            created_at
        FROM push_notification_events
        WHERE status = 'pending'
          AND __ELIGIBLE_PUSH__
          AND NOT EXISTS(SELECT 1 FROM notification_legacy_holds h WHERE h.channel='push' AND h.queue_id=push_notification_events.id)
          AND NOT EXISTS(SELECT 1 FROM notification_delivery_attempts da WHERE da.channel='push' AND da.queue_id=push_notification_events.id AND da.state NOT IN ('disabled','blocked'))
        ORDER BY created_at ASC, id ASC
        LIMIT %s
        """.replace("__ELIGIBLE_PUSH__",gate),
        (*gate_params, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def mark_push_notification_event_sent(
    conn: Any,
    *,
    event_id: int,
    sent_count: int,
    failed_count: int,
    deleted_count: int,
) -> None:
    conn.execute(
        """
        UPDATE push_notification_events
        SET
            status = 'sent',
            sent_at = NOW(),
            sent_count = %s,
            failed_count = %s,
            deleted_count = %s,
            error_message = NULL
        WHERE id = %s
        """,
        (sent_count, failed_count, deleted_count, event_id),
    )


def mark_push_notification_event_failed(
    conn: Any,
    *,
    event_id: int,
    error_message: str,
    sent_count: int = 0,
    failed_count: int = 0,
    deleted_count: int = 0,
) -> None:
    conn.execute(
        """
        UPDATE push_notification_events
        SET
            status = 'failed',
            sent_at = NULL,
            sent_count = %s,
            failed_count = %s,
            deleted_count = %s,
            error_message = %s
        WHERE id = %s
        """,
        (sent_count, failed_count, deleted_count, error_message, event_id),
    )

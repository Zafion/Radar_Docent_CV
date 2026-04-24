from __future__ import annotations

from typing import Any


def upsert_push_subscription(
    conn: Any,
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO push_subscriptions (
            endpoint, p256dh_key, auth_key, is_active
        ) VALUES (%s, %s, %s, TRUE)
        ON CONFLICT(endpoint) DO UPDATE SET
            p256dh_key = EXCLUDED.p256dh_key,
            auth_key = EXCLUDED.auth_key,
            is_active = TRUE,
            updated_at = NOW()
        """,
        (endpoint, p256dh_key, auth_key),
    )


def deactivate_push_subscription(conn: Any, endpoint: str) -> None:
    conn.execute(
        """
        UPDATE push_subscriptions
        SET is_active = FALSE,
            updated_at = NOW()
        WHERE endpoint = %s
        """,
        (endpoint,),
    )


def delete_push_subscription(conn: Any, endpoint: str) -> None:
    conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = %s",
        (endpoint,),
    )


def list_active_push_subscriptions(conn: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT endpoint, p256dh_key, auth_key
        FROM push_subscriptions
        WHERE is_active = TRUE
        ORDER BY id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]
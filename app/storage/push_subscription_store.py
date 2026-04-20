from __future__ import annotations

import sqlite3
from typing import Any


def upsert_push_subscription(
    conn: sqlite3.Connection,
    endpoint: str,
    p256dh_key: str,
    auth_key: str,
) -> None:
    conn.execute(
        """
        INSERT INTO push_subscriptions (
            endpoint, p256dh_key, auth_key, is_active
        ) VALUES (?, ?, ?, 1)
        ON CONFLICT(endpoint) DO UPDATE SET
            p256dh_key = excluded.p256dh_key,
            auth_key = excluded.auth_key,
            is_active = 1,
            updated_at = CURRENT_TIMESTAMP
        """,
        (endpoint, p256dh_key, auth_key),
    )


def deactivate_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute(
        """
        UPDATE push_subscriptions
        SET is_active = 0,
            updated_at = CURRENT_TIMESTAMP
        WHERE endpoint = ?
        """,
        (endpoint,),
    )


def delete_push_subscription(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute(
        "DELETE FROM push_subscriptions WHERE endpoint = ?",
        (endpoint,),
    )


def list_active_push_subscriptions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT endpoint, p256dh_key, auth_key
        FROM push_subscriptions
        WHERE is_active = 1
        ORDER BY id ASC
        """
    ).fetchall()
    return [dict(row) for row in rows]

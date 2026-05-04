from __future__ import annotations

from app.services.push_notifications import is_push_configured, send_push_notification_to_all
from app.storage.db import get_connection
from app.storage.push_event_store import (
    list_pending_push_notification_events,
    mark_push_notification_event_failed,
    mark_push_notification_event_sent,
)


def main() -> None:
    conn = get_connection()
    try:
        configured = is_push_configured()
        events = list_pending_push_notification_events(conn, limit=50)

        print("=" * 100)
        print("push_notifications")
        print(f"Push configurado: {'sí' if configured else 'no'}")
        print(f"Eventos pendientes: {len(events)}")

        if not events:
            conn.commit()
            return

        if not configured:
            for event in events:
                mark_push_notification_event_failed(
                    conn,
                    event_id=int(event["id"]),
                    error_message="push_not_configured",
                )
                print(f"- {event['event_key']}: failed push_not_configured")
            conn.commit()
            return

        for event in events:
            try:
                result = send_push_notification_to_all(
                    conn,
                    title=str(event["title"]),
                    body=str(event["body"]),
                    url=str(event["url"]),
                )
                mark_push_notification_event_sent(
                    conn,
                    event_id=int(event["id"]),
                    sent_count=int(result.get("sent", 0)),
                    failed_count=int(result.get("failed", 0)),
                    deleted_count=int(result.get("deleted", 0)),
                )
                print(f"- {event['event_key']}: {result}")
            except Exception as exc:
                mark_push_notification_event_failed(
                    conn,
                    event_id=int(event["id"]),
                    error_message=str(exc),
                )
                print(f"- {event['event_key']}: failed {exc}")

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn._conn.close()


if __name__ == "__main__":
    main()

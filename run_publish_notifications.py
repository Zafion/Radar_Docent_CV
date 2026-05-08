from __future__ import annotations

from app.services.telegram_notifications import is_telegram_configured, send_telegram_message
from app.storage.db import get_connection
from app.storage.public_alert_store import (
    list_pending_notification_posts,
    mark_notification_post_failed,
    mark_notification_post_published,
)


def main() -> None:
    conn = get_connection()
    try:
        configured = is_telegram_configured()
        posts = list_pending_notification_posts(conn, channel="telegram", limit=50)

        print("=" * 100)
        print("public_notification_publishers")
        print(f"Telegram configurado: {'sí' if configured else 'no'}")
        print(f"Posts Telegram pendientes: {len(posts)}")

        if not posts:
            conn.commit()
            return
        if not configured:
            print("Telegram no configurado: se conservan los posts pendientes.")
            conn.commit()
            return

        for post in posts:
            try:
                result = send_telegram_message(str(post["rendered_text"]))
                message_id = result.get("result", {}).get("message_id")
                mark_notification_post_published(
                    conn,
                    post_id=int(post["id"]),
                    external_message_id=str(message_id) if message_id is not None else None,
                )
                print(f"- {post['event_key']}: published telegram message_id={message_id}")
            except Exception as exc:
                mark_notification_post_failed(conn, post_id=int(post["id"]), error_message=str(exc))
                print(f"- {post['event_key']}: failed {exc}")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn._conn.close()


if __name__ == "__main__":
    main()

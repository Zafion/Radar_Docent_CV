from __future__ import annotations

from app.services.telegram_notifications import (
    is_telegram_configured,
    send_telegram_message,
    send_telegram_photo,
)
from app.storage.db import get_connection
from app.storage.public_alert_store import (
    absolute_public_url,
    list_pending_notification_posts,
    mark_notification_post_failed,
    mark_notification_post_published,
)


TELEGRAM_IMAGE_PATH_BY_EVENT_TYPE: dict[str, str] = {
    "docent_offered_positions_new": "/static/img/telegram/telegram_docent_offered_positions.png",
    "docent_difficult_coverage_new": "/static/img/telegram/telegram_docent_difficult_coverage.png",
    "docent_awards_new": "/static/img/telegram/telegram_docent_awards.png",
    "non_docent_adc_call_new": "/static/img/telegram/telegram_non_docent_adc_call.png",
    "non_docent_adc_award_new": "/static/img/telegram/telegram_non_docent_adc_award.png",
    "non_docent_bags_new": "/static/img/telegram/telegram_non_docent_bags.png",
}


def get_telegram_image_url(event_type: str) -> str | None:
    image_path = TELEGRAM_IMAGE_PATH_BY_EVENT_TYPE.get(event_type)
    if not image_path:
        return None
    return absolute_public_url(image_path)


def build_telegram_photo_caption(text: str) -> str:
    """Telegram photo captions have a 1024 character limit.

    Current Funkcionario notification texts are shorter than that, but this keeps
    the publisher safe if a future message grows.
    """
    max_len = 1024
    if len(text) <= max_len:
        return text

    return text[:1000].rstrip() + "\n\n…"


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
                rendered_text = str(post["rendered_text"])
                event_type = str(post.get("event_type") or "")
                image_url = get_telegram_image_url(event_type)

                if image_url:
                    result = send_telegram_photo(
                        photo_url=image_url,
                        caption=build_telegram_photo_caption(rendered_text),
                    )
                    print(f"- {post['event_key']}: published telegram photo={image_url}")
                else:
                    result = send_telegram_message(rendered_text)
                    print(f"- {post['event_key']}: published telegram text fallback")

                message_id = result.get("result", {}).get("message_id")
                mark_notification_post_published(
                    conn,
                    post_id=int(post["id"]),
                    external_message_id=str(message_id) if message_id is not None else None,
                )
                print(f"  telegram message_id={message_id}")
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

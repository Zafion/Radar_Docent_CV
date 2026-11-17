from __future__ import annotations
import os

from app.services.notification_policy import notifications_enabled
from app.services.notification_transport import dispatch_transport
from app.storage.notification_eligibility_store import (
    claim_notification_delivery, finish_delivery_attempt,
)

from app.services.telegram_notifications import (
    is_telegram_configured,
    get_telegram_channel_id,
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


def safe_delivery_log(message: str) -> None:
    # A broken stdout AFTER commit must not turn a confirmed delivery into failed.
    try:
        print(message)
    except (OSError, ValueError):
        pass


def main() -> None:
    if not notifications_enabled():
        print("Notificaciones desactivadas: se conservan los pendientes.")
        return
    import time
    from app.services.notification_transport import BATCH_SECONDS,MAX_EVENTS,REQUEST_SECONDS
    deadline=time.monotonic()+BATCH_SECONDS
    conn=get_connection()
    try:
        if not is_telegram_configured():return
        posts=list_pending_notification_posts(conn,channel='telegram',limit=MAX_EVENTS)
        conn.commit()
        for post in posts:
            if time.monotonic()>=deadline:break
            image_url=get_telegram_image_url(str(post.get('event_type') or ''))
            authorization=claim_notification_delivery(conn,'telegram',int(post['id']),str(post['event_key']),return_authorization=True,
                delivery_context=dict(image_url=image_url,chat_id=get_telegram_channel_id(),
                    disable_preview=os.getenv('TELEGRAM_DISABLE_WEB_PAGE_PREVIEW','true').strip().lower() in {'1','true','yes','on'},
                    request_seconds=REQUEST_SECONDS,batch_seconds=BATCH_SECONDS))
            if authorization is None:continue
            attempt=authorization['attempt_id']
            token=authorization['token'];snapshot=authorization['snapshot'];content=snapshot['content']
            image=snapshot['delivery_context'].get('image_url')
            request=(dict(kind='telegram_photo',photo_url=image,caption=build_telegram_photo_caption(str(content['rendered_text'])))
                     if image else dict(kind='telegram_message',text=str(content['rendered_text'])))
            request.update(chat_id=snapshot['delivery_context'].get('chat_id'),
                           disable_preview=snapshot['delivery_context'].get('disable_preview',True))
            try:
                result=dispatch_transport(request,timeout=min(REQUEST_SECONDS,deadline-time.monotonic()),deadline=deadline)
                if result.get('not_started'):
                    state='disabled' if result.get('disabled') else 'blocked'
                    finish_delivery_attempt(conn,attempt,state,'transport_not_started',token=token)
                    conn.commit();continue
                message_id=result.get('result',{}).get('message_id')
                if result.get('ok') is not True or type(message_id) is not int or message_id<=0:raise RuntimeError('telegram_delivery_not_confirmed')
                confirmed=finish_delivery_attempt(conn,attempt,'sent',token=token)
                if confirmed:
                    mark_notification_post_published(conn,post_id=int(post['id']),external_message_id=str(message_id))
                conn.commit()
                safe_delivery_log(f"- {post['event_key']}: "+('published telegram' if confirmed else 'obsolete authorization ignored'))
            except Exception:
                conn.rollback()
                if finish_delivery_attempt(conn,attempt,'uncertain','delivery_outcome_uncertain',token=token):
                    mark_notification_post_failed(conn,post_id=int(post['id']),error_message='delivery_outcome_uncertain')
                conn.commit()
        safe_delivery_log('Lote Telegram finalizado; los posts no reclamados permanecen pendientes.')
    except BaseException:
        conn.rollback();raise
    finally:conn.close()


if __name__=='__main__':main()

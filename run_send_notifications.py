"""Dispatch authorized push snapshots without holding PostgreSQL during IO."""
import json
import time
from app.services.notification_policy import notifications_enabled
from app.services.push_notifications import is_push_configured
from app.services.notification_transport import (dispatch_transport,prepare_push_recipients,send_push_snapshot,
    deactivate_expired_recipients,REQUEST_SECONDS,BATCH_SECONDS,MAX_EVENTS,MAX_RECIPIENTS)
from app.storage.db import get_connection
from app.storage.push_event_store import (list_pending_push_notification_events,
    mark_push_notification_event_failed,mark_push_notification_event_sent)
from app.storage.notification_eligibility_store import (
    claim_notification_delivery,finish_delivery_attempt)


def main():
    if not notifications_enabled():
        print('Notificaciones desactivadas: se conservan los pendientes.');return
    deadline=time.monotonic()+BATCH_SECONDS
    remaining_recipients=MAX_RECIPIENTS
    conn=get_connection()
    try:
        if not is_push_configured():return
        events=list_pending_push_notification_events(conn,limit=MAX_EVENTS)
        conn.commit()
        for event in events:
            if time.monotonic()>=deadline or remaining_recipients<=0:break
            recipients,total=prepare_push_recipients(conn)
            recipients=recipients[:remaining_recipients]
            if time.monotonic()>=deadline:break
            authorization=claim_notification_delivery(conn,'push',int(event['id']),str(event['event_key']),return_authorization=True,
                delivery_context=dict(recipient_ids=[s['id'] for s in recipients],total=total,
                                      request_seconds=REQUEST_SECONDS,batch_seconds=BATCH_SECONDS,
                                      recipient_budget=remaining_recipients))
            if authorization is None:continue
            attempt=authorization['attempt_id']
            token=authorization['token'];content=authorization['snapshot']['content']
            try:
                # All values below are detached copies. No connection/cursor is
                # passed to a process that can communicate with a provider.
                result=send_push_snapshot(recipients,total,content,deadline,dispatch=dispatch_transport)
                remaining_recipients-=len(result['recipients'])
                sent=int(result['sent']);failed=int(result['failed']);unknown=int(result['uncertain']);remaining=int(result['not_started'])
                if not sent and not failed and not unknown:
                    finish_delivery_attempt(conn,attempt,'disabled' if result.get('disabled') else 'blocked','transport_not_started',token=token)
                elif sent==total and not failed and not unknown and not remaining:
                    if finish_delivery_attempt(conn,attempt,'sent',json.dumps(result),token=token):
                        mark_push_notification_event_sent(conn,event_id=int(event['id']),sent_count=sent,failed_count=0,deleted_count=0)
                else:
                    state='uncertain' if sent or unknown or remaining else 'failed'
                    if finish_delivery_attempt(conn,attempt,state,json.dumps(result),token=token):
                        deactivate_expired_recipients(conn,recipients,result)
                        mark_push_notification_event_failed(conn,event_id=int(event['id']),error_message='delivery_not_fully_confirmed',sent_count=sent,failed_count=failed,deleted_count=0)
                conn.commit()
            except Exception:
                conn.rollback()
                if finish_delivery_attempt(conn,attempt,'uncertain','delivery_outcome_uncertain',token=token):
                    mark_push_notification_event_failed(conn,event_id=int(event['id']),error_message='delivery_outcome_uncertain')
                conn.commit()
                break  # An unexpected outcome consumes the remaining batch safely.
        print('Lote push finalizado; los eventos no reclamados permanecen pendientes.')
    except BaseException:
        conn.rollback();raise
    finally:conn.close()

if __name__=='__main__':main()

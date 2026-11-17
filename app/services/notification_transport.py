"""Bounded external IO in a fresh interpreter, never a fork with DB handles."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

REQUEST_SECONDS = 10.0
BATCH_SECONDS = 30.0
MAX_EVENTS = 20
MAX_RECIPIENTS = 100
WORKER_MODULE = 'app.services.notification_transport'
_slot = threading.BoundedSemaphore(1)


def dispatch_transport(request, timeout=REQUEST_SECONDS, *, deadline=None):
    from app.services.notification_policy import notifications_enabled
    if not notifications_enabled():
        return {'disabled': True, 'not_started': True}
    if timeout <= 0:
        return {'not_started': True, 'reason': 'budget_exhausted'}
    if not _slot.acquire(blocking=False):
        return {'not_started': True, 'reason': 'transport_busy'}
    process = None
    expires=min(time.monotonic()+min(timeout,REQUEST_SECONDS),deadline if deadline is not None else float('inf'))
    try:
        # Only credentials needed by the transport, not a DSN/PGSERVICE/pool.
        allowed = {'PATH','LANG','LC_ALL','TZ','SYSTEMROOT','ENABLE_NOTIFICATIONS',
                   'TELEGRAM_BOT_TOKEN','TELEGRAM_CHANNEL_ID','TELEGRAM_DISABLE_WEB_PAGE_PREVIEW',
                   'RADAR_PUSH_VAPID_PUBLIC_KEY','RADAR_PUSH_VAPID_PRIVATE_KEY','RADAR_PUSH_VAPID_SUBJECT'}
        env = {k:v for k,v in os.environ.items() if k in allowed}
        process = subprocess.Popen([sys.executable, '-B', '-m', WORKER_MODULE, '--worker'],
            cwd=Path(__file__).resolve().parents[2], env=env, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, close_fds=True)
        if time.monotonic()>=expires or not notifications_enabled():
            process.kill();process.communicate()
            return {'not_started':True,'disabled':not notifications_enabled(),'reason':'not_dispatched'}
        try:
            output,_ = process.communicate(json.dumps(request), timeout=max(.001,expires-time.monotonic()))
        except subprocess.TimeoutExpired:
            process.kill();process.communicate()
            return {'uncertain': True, 'reason': 'transport_deadline'}
        if process.returncode != 0:
            return {'uncertain': True, 'reason': 'transport_worker_exited'}
        result = json.loads(output)
        if not isinstance(result,dict):raise ValueError('invalid_transport_response')
        return result
    except OSError:
        return {'not_started':True,'reason':'worker_not_started'} if process is None else {'uncertain':True,'reason':'worker_io_error'}
    except ValueError:
        return {'uncertain': True, 'reason': 'transport_result_unavailable'}
    finally:
        if process is not None and process.poll() is None:
            process.kill();process.communicate()
        _slot.release()


def prepare_push_recipients(conn):
    # One statement gives a consistent total plus a bounded copy. No auth keys
    # are retained in the authorization journal or logs.
    rows=conn.execute('''SELECT id,endpoint,p256dh_key,auth_key,count(*) OVER() AS total
        FROM push_subscriptions WHERE is_active=TRUE ORDER BY id LIMIT %s''',(MAX_RECIPIENTS,)).fetchall()
    conn.commit()
    return [dict(r) for r in rows], int(rows[0]['total']) if rows else 0


def send_push_snapshot(recipients,total,content,deadline,dispatch=dispatch_transport):
    results=[];sent=failed=uncertain=0;disabled=False;expired=[]
    for sub in recipients:
        if time.monotonic()>=deadline:break
        result=dispatch(dict(kind='push',subscription={k:sub[k] for k in ('endpoint','p256dh_key','auth_key')},
                             title=str(content['title']),body=str(content['body']),url=str(content['url'])),
                        timeout=min(REQUEST_SECONDS,deadline-time.monotonic()),deadline=deadline)
        if result.get('not_started'):
            disabled=bool(result.get('disabled'))
            break
        if result.get('ok') is True:
            sent+=1;state='sent'
        elif result.get('ok') is False and not result.get('uncertain'):
            failed+=1;state='failed'
        else:
            uncertain+=1;state='uncertain'
        results.append(dict(recipient_id=sub['id'],state=state))
        if result.get('expired') and state=='failed':expired.append(sub['id'])
        if result.get('reason')=='transport_deadline':break
    return dict(sent=sent,failed=failed,uncertain=uncertain,not_started=max(0,total-len(results)),
                recipients=results,total=total,disabled=disabled,expired_recipient_ids=expired)


def deactivate_expired_recipients(conn,recipients,result):
    """After IO only; never delete a subscription or deactivate refreshed keys."""
    expired=set(result.get('expired_recipient_ids',()))
    count=0
    for sub in recipients:
        if sub['id'] not in expired:continue
        count+=conn.execute('''UPDATE push_subscriptions SET is_active=FALSE,updated_at=now()
            WHERE id=%s AND endpoint=%s AND p256dh_key=%s AND auth_key=%s AND is_active=TRUE''',
            (sub['id'],sub['endpoint'],sub['p256dh_key'],sub['auth_key'])).rowcount
    return count


def worker(request):
    # This interpreter has no inherited descriptors or DB environment. Only a
    # single external request is allowed; never import an orchestration entrypoint.
    from app.services.notification_policy import notifications_enabled
    if not notifications_enabled():return {'disabled':True,'not_started':True}
    kind=request['kind']
    if kind=='push':
        from pywebpush import webpush,WebPushException
        from app.services.push_notifications import VAPID_PRIVATE_KEY,VAPID_SUBJECT
        sub=request['subscription']
        try:
            response=webpush(subscription_info={'endpoint':sub['endpoint'],'keys':{'p256dh':sub['p256dh_key'],'auth':sub['auth_key']}},
                data=json.dumps({k:request[k] for k in ('title','body','url')}),vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims={'sub':VAPID_SUBJECT},timeout=REQUEST_SECONDS)
        except WebPushException as exc:
            status=getattr(getattr(exc,'response',None),'status_code',None)
            if status in (404,410):return {'ok':False,'expired':True}
            if status is not None and 400<=status<500 and status not in (408,429):return {'ok':False}
            raise
        return {'ok':200<=response.status_code<300}
    from app.services.telegram_notifications import _telegram_json_request
    if not request.get('chat_id'):return {'not_started':True,'reason':'telegram_destination_missing'}
    if kind=='telegram_photo':return _telegram_json_request('sendPhoto',dict(chat_id=request['chat_id'],photo=request['photo_url'],caption=request['caption']))
    if kind=='telegram_message':return _telegram_json_request('sendMessage',dict(chat_id=request['chat_id'],text=request['text'],disable_web_page_preview=request['disable_preview']))
    raise ValueError('unknown_transport')


if __name__=='__main__':
    try:reply=worker(json.load(sys.stdin))
    except Exception:reply={'uncertain':True,'reason':'transport_exception'}
    print(json.dumps(reply))

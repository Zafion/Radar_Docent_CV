"""DR03 matrix exercised inside the owned PostgreSQL runner, never a real DSN."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack,redirect_stdout
from datetime import datetime,timedelta,timezone
import io,json,os,time
from unittest.mock import Mock,patch


def exercise(c,cluster):
    import run_send_notifications as push
    import run_publish_notifications as telegram
    from app.services import notification_transport as transport
    from app.storage.db import get_connection
    from app.services.notification_eligibility import reconcile_notifications
    from app.storage.notification_eligibility_store import claim_notification_delivery,load_delivery_authorization
    from tests.postgres_notification_eligibility_validation import parse_document
    from app.services.parse_plan import preview,execute_plan
    from app.services.non_docent_parser import NonDocentParserService
    from tests.test_non_docent_extraction_safety import AWARD

    def new():
        doc,version=c.doc();parse_document(c,doc);reconcile_notifications();c.db.commit()
        key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(doc,))
        return doc,key
    def state(key):return c.scalar("SELECT state FROM notification_delivery_attempts WHERE event_key=%s AND channel='push'",(key,))
    def send(module,key,provider):
        listing='list_pending_push_notification_events' if module is push else 'list_pending_notification_posts'
        original=getattr(module,listing)
        def selected(conn,**kw):return [r for r in original(conn,**kw) if r['event_key']==key]
        configured='is_push_configured' if module is push else 'is_telegram_configured'
        with patch.object(module,listing,selected),patch.object(module,configured,return_value=True),patch.object(module,'dispatch_transport',side_effect=provider),redirect_stdout(io.StringIO()):module.main()
        c.db.commit()
    def reparse(doc):
        digest=c.scalar('SELECT v.sha256 FROM document_versions v JOIN documents d ON d.document_version_id=v.id WHERE d.id=%s',(doc,))
        plan=preview(shas=[digest],reprocess=True)
        with patch.object(NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):
            result=execute_plan(plan,authorized=True)
        c.db.commit();return result

    doc,key=new();provider=Mock(return_value={'ok':True})
    send(push,key,provider);send(push,key,provider)
    c.check('immediate_ack_sent_once',state(key)=='sent' and provider.call_count==1)
    c.check('ack_not_lost_to_later_revocation',c.scalar('SELECT authorization_snapshot IS NOT NULL FROM notification_delivery_attempts WHERE event_key=%s',(key,)))
    doc,key=new();provider=Mock(return_value={'ok':True,'result':{'message_id':17}})
    send(telegram,key,provider);send(telegram,key,provider)
    c.check('telegram_ack_sent_once',provider.call_count==1 and c.scalar("SELECT state FROM notification_delivery_attempts WHERE event_key=%s AND channel='telegram'",(key,))=='sent')

    doc,key=new();c.db.execute("UPDATE notification_document_state SET delivery_hold_reason='synthetic_revoked' WHERE document_id=%s",(doc,));c.db.commit()
    provider=Mock(return_value={'ok':True});send(push,key,provider)
    c.check('revocation_before_authorization_sends_nothing',provider.call_count==0 and c.scalar('SELECT count(*) FROM notification_delivery_attempts WHERE event_key=%s',(key,))==0)
    doc,key=new()
    def revoke_inflight(*a,**kw):
        result=reparse(doc)
        c.check('real_dr02_replay_can_run_during_dispatch',sum(s.success_count for s in result.parser_summaries)==1)
        return {'ok':True}
    send(push,key,revoke_inflight)
    c.check('ack_after_revocation_preserves_real_outcome',state(key)=='sent')
    c.check('snapshot_keeps_original_validation',c.scalar("SELECT (a.authorization_snapshot->'identity'->>'validation_run_id')::bigint<>ns.validation_run_id FROM notification_delivery_attempts a JOIN notification_document_state ns ON ns.queued_event_key=a.event_key WHERE a.event_key=%s",(key,)))
    provider=Mock();send(push,key,provider);c.check('revoked_document_never_reauthorized',provider.call_count==0)

    doc,key=new();qid=c.scalar('SELECT id FROM push_notification_events WHERE event_key=%s',(key,))
    attempt=claim_notification_delivery(c.db,'push',qid,key);load_delivery_authorization(c.db,attempt)
    provider=Mock();send(push,key,provider)
    c.check('crash_after_authorization_before_send_is_held',state(key)=='sending' and provider.call_count==0)
    doc,key=new();provider=Mock(return_value={'ok':True})
    try:
        with patch.object(push,'finish_delivery_attempt',side_effect=KeyboardInterrupt('synthetic_after_ack')):send(push,key,provider)
    except KeyboardInterrupt:pass
    c.db.commit();c.check('crash_after_ack_before_commit_stays_sending',provider.call_count==1 and state(key)=='sending')
    repeat=Mock();send(push,key,repeat);c.check('crashed_ack_not_automatically_repeated',repeat.call_count==0)

    doc,key=new();provider=Mock(side_effect=RuntimeError('synthetic_connection_error'))
    send(push,key,provider);send(push,key,provider)
    c.check('connection_error_uncertain_no_retry',state(key)=='uncertain' and provider.call_count==1)
    doc,key=new();provider=Mock(return_value={'ok':False})
    send(push,key,provider);send(push,key,provider)
    c.check('failed_state_not_automatically_retried',state(key)=='failed' and provider.call_count==1 and c.scalar('SELECT sent_count FROM push_notification_events WHERE event_key=%s',(key,))==0)
    doc,key=new();provider=Mock(return_value={'disabled':True,'not_started':True})
    send(push,key,provider);old=c.scalar('SELECT authorization_token FROM notification_delivery_attempts WHERE event_key=%s',(key,))
    c.check('known_not_started_keeps_pending',state(key)=='disabled' and c.scalar('SELECT status FROM push_notification_events WHERE event_key=%s',(key,))=='pending')
    send(push,key,lambda *a,**kw:{'ok':True});newtoken=c.scalar('SELECT authorization_token FROM notification_delivery_attempts WHERE event_key=%s',(key,))
    c.check('controlled_retry_uses_fresh_token',state(key)=='sent' and old!=newtoken)

    # Actual slow/never-returning child, with a real PostgreSQL connection open
    # in the parent. Its stdout certifies that neither DB environment nor sockets inherited.
    doc,key=new();c.db.execute("UPDATE push_notification_events SET body='slow' WHERE event_key=%s",(key,));c.db.commit()
    marker=c.output/'slow_started';real_dispatch=transport.dispatch_transport
    def slow(request,**kw):
        result=real_dispatch(dict(request,started_marker=str(marker),delay=2),**kw)
        c.check('child_does_not_inherit_actual_parent_database_handles',not result.get('inherited_socket',True) and not result.get('inherited_database_env',True))
        return result
    with patch.object(transport,'WORKER_MODULE','tests.notification_transport_stub'),ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(send,push,key,slow)
        end=time.monotonic()+5
        while not marker.exists() and time.monotonic()<end:time.sleep(.01)
        if not marker.exists():raise RuntimeError('fake_provider_did_not_start')
        started=time.monotonic();result=reparse(doc)
        c.check('real_parser_finishes_while_slow_child_is_inflight',time.monotonic()-started<1.5 and not future.done())
        future.result(timeout=5)
    c.db.commit();c.check('slow_child_ack_confirmed',state(key)=='sent')
    doc,key=new();c.db.execute("UPDATE push_notification_events SET body='never' WHERE event_key=%s",(key,));c.db.commit()
    started=time.monotonic()
    with patch.object(transport,'WORKER_MODULE','tests.notification_transport_stub'):send(push,key,real_dispatch)
    elapsed=time.monotonic()-started
    c.check('never_responding_provider_stopped_at_ten_seconds',9<=elapsed<12 and state(key)=='uncertain')
    repeat=Mock();send(push,key,repeat);c.check('timed_out_request_never_retried',repeat.call_count==0)
    doc,key=new();c.db.execute("UPDATE push_notification_events SET body='slow' WHERE event_key=%s",(key,));c.db.commit()
    with patch.object(transport,'WORKER_MODULE','tests.notification_transport_stub'),patch.object(push,'BATCH_SECONDS',.12):send(push,key,real_dispatch)
    c.check('execution_budget_cancels_inflight_request',state(key)=='uncertain')

    # Two independent sender sessions: the first holds only a durable claim
    # during IO; the second completes with no second call to its provider.
    import threading
    doc,key=new();entered=threading.Event();release=threading.Event()
    def held(*a,**kw):entered.set();release.wait(5);return {'ok':True}
    # Avoid sharing patched module functions between threads: selectors are
    # restricted once and the dispatcher routes first/second calls by thread.
    original=push.list_pending_push_notification_events
    def selected(conn,**kw):return [r for r in original(conn,**kw) if r['event_key']==key]
    calls=[]
    def one_provider(*a,**kw):calls.append(1);return held()
    with patch.object(push,'list_pending_push_notification_events',selected),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',side_effect=one_provider),ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(push.main)
        if not entered.wait(5):raise RuntimeError('first_sender_not_started')
        second=pool.submit(push.main);second.result(timeout=3);release.set();first.result(timeout=3)
    c.db.commit();c.check('two_sender_sessions_only_one_dispatch',len(calls)==1 and state(key)=='sent')

    # Legacy is never promoted or marked delivered.
    from app.storage.public_alert_store import enqueue_public_alert
    from app.storage.push_event_store import enqueue_push_notification_event
    enqueue_public_alert(c.db,event_key='legacy_dispatch',event_type='old',title='old',summary='old',public_url='/')
    enqueue_push_notification_event(c.db,event_key='legacy_dispatch',event_type='old',title='old',body='old',url='/');c.db.commit()
    legacy=c.scalar("SELECT md5(row_to_json(p)::text) FROM push_notification_events p WHERE event_key='legacy_dispatch'")
    repeat=Mock();send(push,'legacy_dispatch',repeat);send(telegram,'legacy_dispatch',repeat)
    c.check('legacy_queues_unchanged',repeat.call_count==0 and legacy==c.scalar("SELECT md5(row_to_json(p)::text) FROM push_notification_events p WHERE event_key='legacy_dispatch'"))
    doc,key=new();repeat=Mock()
    with patch.dict(os.environ,{'ENABLE_NOTIFICATIONS':'0'}):send(push,key,repeat);send(telegram,key,repeat)
    c.check('disabled_switch_does_not_authorize',repeat.call_count==0)
    with patch.dict(os.environ,{'NOTIFICATION_CUTOVER_UTC':(datetime.now(timezone.utc)+timedelta(hours=1)).isoformat()}):send(push,key,repeat)
    c.check('cutover_still_blocks_new_authorization',repeat.call_count==0)

    c.db.execute("INSERT INTO push_subscriptions(endpoint,p256dh_key,auth_key) VALUES('https://example.invalid/second','synthetic','synthetic')");c.db.commit()
    doc,key=new();provider=Mock(side_effect=[{'ok':True},{'uncertain':True}]);send(push,key,provider)
    c.check('partial_keeps_only_confirmed_recipient_count',state(key)=='uncertain' and c.scalar('SELECT sent_count FROM push_notification_events WHERE event_key=%s',(key,))==1)
    outcome=json.loads(c.scalar('SELECT reason FROM notification_delivery_attempts WHERE event_key=%s',(key,)))
    c.check('partial_recipient_outcomes_auditable',outcome['sent']==1 and outcome['uncertain']==1 and outcome['not_started']==0)
    first_sub=c.scalar('SELECT min(id) FROM push_subscriptions WHERE is_active=TRUE')
    count=c.scalar('SELECT count(*) FROM push_subscriptions')
    doc,key=new();provider=Mock(side_effect=[{'ok':False,'expired':True},{'ok':True}]);send(push,key,provider)
    c.check('expired_endpoint_deactivated_after_io_without_deletion',c.scalar('SELECT count(*) FROM push_subscriptions')==count and not c.scalar('SELECT is_active FROM push_subscriptions WHERE id=%s',(first_sub,)))
    c.db.execute("INSERT INTO push_subscriptions(endpoint,p256dh_key,auth_key) VALUES('https://example.invalid/replacement','synthetic','synthetic')");c.db.commit()
    doc,key=new();updated=[]
    def refresh_while_inflight(request,**kw):
        if not updated:
            updated.append(request['subscription']['endpoint'])
            c.db.execute("UPDATE push_subscriptions SET auth_key='renewed' WHERE endpoint=%s",(updated[0],));c.db.commit()
            return {'ok':False,'expired':True}
        return {'ok':True}
    send(push,key,refresh_while_inflight)
    c.check('late_expiry_cannot_deactivate_renewed_subscription',c.scalar("SELECT is_active AND auth_key='renewed' FROM push_subscriptions WHERE endpoint=%s",(updated[0],)))

    # More than20 eligible events: unclaimed ones stay pending, no fake delivery.
    keys=[new()[1] for _ in range(21)]
    allowed=set(keys);original=push.list_pending_push_notification_events
    def batch(conn,**kw):return [r for r in original(conn,limit=1000) if r['event_key'] in allowed][:kw['limit']]
    with patch.object(push,'list_pending_push_notification_events',batch),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',return_value={'ok':True}),redirect_stdout(io.StringIO()):push.main()
    c.db.commit();c.check('twenty_event_limit_preserves_remaining_queue',c.scalar('SELECT count(*) FROM notification_delivery_attempts WHERE event_key=ANY(%s)',(keys,))==20 and c.scalar('SELECT status FROM push_notification_events WHERE event_key=%s',(keys[-1],))=='pending')
    for i in range(100):c.db.execute('INSERT INTO push_subscriptions(endpoint,p256dh_key,auth_key) VALUES(%s,\'synthetic\',\'synthetic\')',(f'https://example.invalid/extra/{i}',))
    c.db.commit();doc,key=new();otherdoc,otherkey=new();provider=Mock(return_value={'ok':True})
    allowed={key,otherkey};original=push.list_pending_push_notification_events
    def capped(conn,**kw):return [r for r in original(conn,limit=1000) if r['event_key'] in allowed][:kw['limit']]
    with patch.object(push,'list_pending_push_notification_events',capped),patch.object(push,'is_push_configured',return_value=True),patch.object(push,'dispatch_transport',provider),redirect_stdout(io.StringIO()):push.main()
    c.db.commit()
    outcome=json.loads(c.scalar('SELECT reason FROM notification_delivery_attempts WHERE event_key=%s',(key,)))
    c.check('hundred_recipient_cap_not_silent_discard',provider.call_count==100 and outcome['sent']==100 and outcome['not_started']==2 and state(key)=='uncertain')
    c.check('confirmed_count_does_not_include_unstarted',c.scalar('SELECT sent_count FROM push_notification_events WHERE event_key=%s',(key,))==100)
    c.check('recipient_limit_also_applies_across_the_whole_execution',c.scalar('SELECT count(*) FROM notification_delivery_attempts WHERE event_key=%s',(otherkey,))==0 and c.scalar('SELECT status FROM push_notification_events WHERE event_key=%s',(otherkey,))=='pending')

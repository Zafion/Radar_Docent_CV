"""DR-03 regression: only a NEW owned PostgreSQL cluster and simulated providers."""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime,timedelta,timezone
import io,json,os
from pathlib import Path
import sys,time
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.postgres_notification_eligibility_validation import Checks,parse_document


def main(output,expect_blocked=False,phase='B'):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'auditoria_local/resultados') or output.exists():raise ValueError('new ignored output required')
    output.mkdir(parents=True);isolated.OUT=output;c=None
    report={'status':'running','existing_database_used':False,'external_sends':0}
    try:
        with isolated.FreshCluster() as cluster:
            now=datetime.now(timezone.utc)
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='1',TELEGRAM_BOT_TOKEN='synthetic',TELEGRAM_CHANNEL_ID='@synthetic',RADAR_PUSH_VAPID_PUBLIC_KEY='',RADAR_PUSH_VAPID_PRIVATE_KEY='',INDEXNOW_KEY='',PGPASSFILE=str(cluster.root/'absent'),NOTIFICATION_CUTOVER_UTC=(now-timedelta(hours=1)).isoformat())
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                spies=list(isolated.connection_guards(cluster,stack))
                from app.storage.db import init_db,get_connection,close_pool
                from app.services.notification_eligibility import reconcile_notifications
                from app.storage.notification_eligibility_store import processing_started
                schema=ROOT/'app/storage/schema.sql'
                if phase=='A':
                    schema=cluster.root/'pre_dr03.sql';schema.write_text((ROOT/'app/storage/schema.sql').read_text().split('-- DR03 dispatch authorization')[0])
                init_db(str(schema));db=get_connection();c=Checks(db,output)
                if phase=='A':
                    db.execute("INSERT INTO notification_delivery_attempts(channel,queue_id,event_key,state) VALUES('push',999,'legacy','sending')");db.commit()
                    migration=(ROOT/'app/storage/migrations/20261106_notification_dispatch.sql').read_text()
                    try:db._conn.execute(migration+' SELECT missing_dispatch_column FROM notification_delivery_attempts;')
                    except psycopg.errors.UndefinedColumn:db.rollback()
                    c.check('failed_upgrade_rolls_back_all_columns',c.scalar("SELECT count(*) FROM information_schema.columns WHERE table_name='notification_delivery_attempts' AND column_name='authorization_token'")==0)
                    db._conn.execute(migration);db.commit();db._conn.execute(migration);db.commit()
                    c.check('legacy_attempt_unchanged_and_unauthorized',c.scalar("SELECT state='sending' AND authorization_token IS NULL AND authorization_snapshot IS NULL FROM notification_delivery_attempts WHERE event_key='legacy'"))

                stack.callback(close_pool);stack.callback(db.close)
                doc,version=c.doc();parse_document(c,doc);reconcile_notifications();db.execute("INSERT INTO push_subscriptions(endpoint,p256dh_key,auth_key) VALUES('https://example.invalid/push','synthetic','synthetic')");db.commit()
                if phase=='A':
                    from app.storage.notification_eligibility_store import claim_notification_delivery,load_delivery_authorization,finish_delivery_attempt
                    key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(doc,))
                    qid=c.scalar('SELECT id FROM push_notification_events WHERE event_key=%s',(key,))
                    auth=claim_notification_delivery(db,'push',qid,key,return_authorization=True)
                    attempt=auth['attempt_id']
                    c.check('snapshot_document_sha_validation_and_content',auth['snapshot']['identity']['document_id']==doc and bool(auth['snapshot']['identity']['sha256']) and bool(auth['snapshot']['identity']['validation_run_id']) and bool(auth['snapshot']['content']['body']) and bool(auth['snapshot']['cutover']))
                    from psycopg.pq import TransactionStatus
                    c.check('authorization_committed_and_idle',db._conn.info.transaction_status==TransactionStatus.IDLE)
                    c.check('obsolete_token_cannot_confirm',not finish_delivery_attempt(db,attempt,'sent',token='00000000-0000-0000-0000-000000000000'))
                    c.check('legacy_worker_without_token_cannot_confirm',not finish_delivery_attempt(db,attempt,'sent'))
                    finish_delivery_attempt(db,attempt,'disabled',token=auth['token']);db.commit()
                    auth2=claim_notification_delivery(db,'push',qid,key,return_authorization=True);attempt2=auth2['attempt_id']
                    c.check('retry_known_not_started_new_generation',attempt==attempt2 and auth2['token']!=auth['token'])
                    c.check('previous_generation_fenced',not finish_delivery_attempt(db,attempt2,'sent',token=auth['token']))
                    db.execute("UPDATE notification_document_state SET delivery_hold_reason='synthetic_revocation' WHERE document_id=%s",(doc,));db.commit()
                    c.check('inflight_authorization_can_confirm_after_revocation',finish_delivery_attempt(db,attempt2,'sent',token=auth2['token']))
                    db.commit()
                    c.check('history_preserves_both_snapshot_generations',c.scalar('SELECT count(DISTINCT authorization_token) FROM notification_delivery_history WHERE attempt_id=%s',(attempt,))==2)
                else:
                    import run_send_notifications as sender
                    connections=[];observed={}
                    def factory():
                        connection=get_connection();connections.append(connection);return connection
                    def slow(*args,**kwargs):
                        time.sleep(.05)
                        from psycopg.pq import TransactionStatus
                        observed['open_transaction']=connections[-1]._conn.info.transaction_status!=TransactionStatus.IDLE
                        other=get_connection()
                        try:
                            other.execute("SET LOCAL lock_timeout='100ms'")
                            try:processing_started(other,version)
                            except psycopg.errors.LockNotAvailable:observed['parser_blocked']=True
                            else:observed['parser_blocked']=False
                            other.rollback()
                        finally:other.close()
                        return {'ok':True}
                    with patch.object(sender,'get_connection',factory),patch.object(sender,'is_push_configured',return_value=True),patch.object(sender,'dispatch_transport',side_effect=slow),redirect_stdout(io.StringIO()):sender.main()
                    c.check('slow_provider_parser_lock_contract',observed.get('parser_blocked')==expect_blocked)
                    c.check('network_transaction_contract',observed.get('open_transaction')==expect_blocked)
                    report['slow_provider_observation']=observed
                    c.check('confirmed_delivery_recorded',c.scalar("SELECT count(*) FROM notification_delivery_attempts WHERE state='sent'")==1)
                    c.check('no_real_transports',all(spy.call_count==0 for _,spy in spies))
                if phase=='C':
                    from tests.notification_dispatch_scenarios import exercise
                    exercise(c,cluster)
                report.update(status='passed' if all(x['passed'] for x in c.checks) else 'failed',checks=c.checks)
        report['cluster_removed']=True
    except BaseException as exc:report.update(status='blocked_or_failed',error=isolated.safe_error(exc),checks=c.checks if c else [])
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1


def finish_report(output,report):
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
    print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True);parser.add_argument('--expect-blocked',action='store_true')
    parser.add_argument('--phase',choices=('A','B','C'),default='B')
    args=parser.parse_args();raise SystemExit(main(args.output_dir,args.expect_blocked,args.phase))

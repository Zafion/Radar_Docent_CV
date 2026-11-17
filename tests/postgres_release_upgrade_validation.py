"""Accumulated DR01/F07/DR03 upgrade, full post-upgrade dump/restore. NEW PG only."""
import argparse
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.postgres_notification_eligibility_validation import Checks, parse_document


def snapshot(db):
    tables=[r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
    data={t:db.execute('SELECT md5(coalesce(string_agg(to_jsonb(x)::text,\'|\' ORDER BY to_jsonb(x)::text),\'\')) FROM "'+t+'" x').fetchone()[0] for t in tables}
    queries={
        'indexes':"SELECT c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1",
        'constraints':"SELECT r.relname,c.conname,pg_get_constraintdef(c.oid),c.convalidated FROM pg_constraint c JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        'functions':"SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' ORDER BY 1,2",
        'triggers':"SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY 1,2",
        'sequences':"SELECT sequencename,last_value,start_value,increment_by FROM pg_sequences WHERE schemaname='public' ORDER BY 1",
        'normalization':"SELECT public.normalize_text(' ÀÉ Ç Ñ  '),public.normalize_text(NULL),public.normalize_text('SINTÈTIC  GÜELL')",
    }
    return dict(data=data,**{k:[list(row.values()) for row in db.execute(sql).fetchall()] for k,sql in queries.items()})


def main(output):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'auditoria_local/resultados') or output.exists():raise ValueError('new ignored output required')
    output.mkdir(parents=True);isolated.OUT=output
    report={'status':'running','existing_database_used':False,'external_sends':0};all_checks=[]
    legacy_path=ROOT/'release/schema_pre_audit.sql'
    legacy=legacy_path.read_text() if legacy_path.exists() else subprocess.check_output(['git','show','main:app/storage/schema.sql'],cwd=ROOT,text=True)
    chain='\n'.join((ROOT/'app/storage/migrations'/name).read_text() for name in [
        '20261106_normalize_text_restore.sql','20261105_notification_eligibility.sql','20261106_notification_dispatch.sql'])
    backup=output/'post_upgrade_synthetic.dump';before=None;key=None;token=None;attempt=None
    try:
        for stage in ('upgrade','restore'):
            with isolated.FreshCluster() as cluster:
                env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
                env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='1',TELEGRAM_BOT_TOKEN='',TELEGRAM_CHANNEL_ID='',RADAR_PUSH_VAPID_PUBLIC_KEY='',RADAR_PUSH_VAPID_PRIVATE_KEY='',INDEXNOW_KEY='',PGPASSFILE=str(cluster.root/'absent'),NOTIFICATION_CUTOVER_UTC=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())
                with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                    import psycopg
                    with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                        cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                    spies=list(isolated.connection_guards(cluster,stack))
                    from app.storage.db import get_connection,close_pool
                    from app.storage.public_alert_store import enqueue_public_alert,list_public_alerts,list_pending_notification_posts
                    from app.storage.push_event_store import enqueue_push_notification_event,list_pending_push_notification_events
                    from app.services.notification_eligibility import reconcile_notifications
                    from app.storage.notification_eligibility_store import claim_notification_delivery,finish_delivery_attempt
                    db=get_connection();stack.callback(close_pool);stack.callback(db.close)
                    c=Checks(db,output)
                    if stage=='upgrade':
                        db._conn.execute(legacy)
                        db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('legacy','https://example.invalid','synthetic')")
                        vid=c.scalar("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at) VALUES(%s,'legacy.pdf','legacy.pdf','synthetic.pdf',1,now()) RETURNING id",('e'*64,))
                        db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,(SELECT id FROM sources WHERE source_key='legacy'),'unknown','synthetic')",(vid,))
                        enqueue_public_alert(db,event_key='legacy',event_type='synthetic',title='synthetic',summary='synthetic',public_url='/')
                        enqueue_push_notification_event(db,event_key='legacy',event_type='synthetic',title='synthetic',body='synthetic',url='/')
                        db.commit();old=snapshot(db);db.commit()
                        try:db._conn.execute(chain+' SELECT missing_release_column FROM document_versions;')
                        except psycopg.errors.UndefinedColumn:db.rollback()
                        c.check('all_three_migrations_rollback_as_one_transaction',snapshot(db)==old)
                        db.commit();db._conn.execute(chain);db.commit()
                        c.check('migration_order_snapshot_columns_and_trace',c.scalar("SELECT count(*) FROM information_schema.columns WHERE table_name IN ('notification_delivery_attempts','notification_delivery_history') AND column_name IN ('authorization_token','authorization_snapshot')")==4 and 'NEW.authorization_token' in c.scalar("SELECT pg_get_functiondef('trace_notification_delivery()'::regprocedure)"))
                        once=snapshot(db);db.commit();db._conn.execute(chain);db.commit()
                        c.check('whole_migration_chain_idempotent',snapshot(db)==once)
                        for table,digest in old['data'].items():
                            if table!='document_versions':c.check('legacy_table_preserved_'+table,snapshot(db)['data'][table]==digest)
                        c.check('legacy_new_intent_unknown',c.scalar('SELECT notification_ingest_intent FROM document_versions WHERE id=%s',(vid,))=='unknown')
                        reconcile_notifications();db.commit()
                        c.check('historical_queues_retained_not_marked_sent',c.scalar('SELECT count(*) FROM notification_legacy_holds')==2 and c.scalar("SELECT count(*) FROM notification_posts WHERE status='pending'")==1 and c.scalar("SELECT count(*) FROM push_notification_events WHERE status='pending'")==1)
                        c.check('legacy_hidden_from_compatible_readers',not list_public_alerts(db) and not list_pending_push_notification_events(db))
                        live,live_version=c.doc();parse_document(c,live);reconcile_notifications();db.commit()
                        key=c.scalar('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s',(live,))
                        queue=c.scalar('SELECT id FROM push_notification_events WHERE event_key=%s',(key,))
                        auth=claim_notification_delivery(db,'push',queue,key,return_authorization=True)
                        token=auth['token'];attempt=auth['attempt_id']
                        c.check('authorized_snapshot_before_transport',auth['snapshot']['identity']['document_id']==live and auth['snapshot']['identity']['document_version_id']==live_version)
                        finish_delivery_attempt(db,attempt,'uncertain',reason='synthetic_ambiguous_delivery',token=token);db.commit()
                        historical,_=c.doc(intent='historical_import');parse_document(c,historical);reconcile_notifications();db.commit()
                        c.check('historical_replay_has_no_queue',c.scalar('SELECT queued_event_key IS NULL FROM notification_document_state WHERE document_id=%s',(historical,)))
                        c.check('dr03_tokens_and_snapshot_in_history',c.scalar('SELECT count(*) FROM notification_delivery_history WHERE attempt_id=%s AND authorization_token=%s AND authorization_snapshot IS NOT NULL',(attempt,token))==2)
                        before=snapshot(db);db.commit()
                        (output/'before_backup.json').write_text(json.dumps(before,indent=2,default=str)+'\n')
                        cluster.command('pg_dump','--format=custom','--file='+str(backup),'--dbname='+cluster.dsn())
                        cluster.command('pg_restore','--list',str(backup))
                        c.check('post_upgrade_backup_created',backup.stat().st_size>0)
                        report['backup_sha256']=hashlib.sha256(backup.read_bytes()).hexdigest()
                    else:
                        cluster.command('pg_restore','--exit-on-error','--single-transaction','--dbname='+cluster.dsn(),str(backup))
                        db.execute('SET LOCAL search_path=pg_catalog')
                        c.check('restored_restricted_search_path_normalization',db.execute("SELECT public.normalize_text(' ÀÉ Ç Ñ ')").fetchone()[0]=='ae c n');db.rollback()
                        restored=snapshot(db)
                        (output/'after_restore.json').write_text(json.dumps(restored,indent=2,default=str)+'\n')
                        for section in before:c.check('full_restore_identical_'+section,before[section]==restored[section])
                        c.check('all_restored_indexes_ready_valid',all(row[2] and row[3] for row in restored['indexes']))
                        c.check('restored_uncertain_not_reclaimed',claim_notification_delivery(db,'push',queue,key,return_authorization=True) is None)
                        db.rollback()
                        c.check('restored_legacy_not_deliverable',not any(r['event_key']=='legacy' for r in list_pending_push_notification_events(db)) and not any(r['event_key']=='legacy' for r in list_pending_notification_posts(db,channel='telegram')))
                        c.check('stale_ack_cannot_overwrite_uncertain',not finish_delivery_attempt(db,attempt,'sent',token=token));db.rollback()
                        # Recovery is compatible code + held transports, never old
                        # publishers or deletion of new state/history/schema.
                        env['ENABLE_NOTIFICATIONS']='0'
                        with patch.dict(os.environ,env,clear=True):
                            c.check('recovery_disabled_transport_cannot_authorize',claim_notification_delivery(db,'push',queue,key,return_authorization=True) is None)
                        db.rollback()
                        after_recovery=snapshot(db)
                        c.check('recovery_preserves_delivery_and_legacy_history',all(after_recovery[k]==restored[k] for k in restored if k!='sequences'))
                        c.check('conflict_attempt_never_rewinds_sequences',all(after[1] is None or before[1] is None or after[1]>=before[1] for before,after in zip(restored['sequences'],after_recovery['sequences'])))
                    c.check(stage+'_zero_external_transports',all(spy.call_count==0 for _,spy in spies))
                    all_checks.extend(dict(name=stage+'_'+x['name'],passed=x['passed']) for x in c.checks)
                report[stage+'_cluster_removed']=False
            report[stage+'_cluster_removed']=not cluster.root.exists()
        report.update(status='passed' if all(x['passed'] for x in all_checks) else 'failed',checks=all_checks)
    except BaseException as exc:
        report.update(status='blocked_or_failed',error=isolated.safe_error(exc),checks=all_checks)
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],len(all_checks),flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    raise SystemExit(main(p.parse_args().output_dir))

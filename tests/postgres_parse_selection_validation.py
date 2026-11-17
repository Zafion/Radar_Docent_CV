"""DR-02 actual execution, read-only planning and two-process claims; NEW PG only."""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import threading
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.postgres_notification_eligibility_validation import Checks


def main(output):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'auditoria_local/resultados') or output.exists():
        raise ValueError('new ignored output required')
    output.mkdir(parents=True);isolated.OUT=output
    report={'status':'running','existing_database_used':False,'external_sends':0};c=None
    try:
        with isolated.FreshCluster() as cluster:
            now=datetime.now(timezone.utc)
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0',TELEGRAM_BOT_TOKEN='',TELEGRAM_CHANNEL_ID='',
                       RADAR_PUSH_VAPID_PUBLIC_KEY='',RADAR_PUSH_VAPID_PRIVATE_KEY='',INDEXNOW_KEY='',
                       PGPASSFILE=str(cluster.root/'absent'),NOTIFICATION_CUTOVER_UTC=(now-timedelta(hours=1)).isoformat())
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                    cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                spies=list(isolated.connection_guards(cluster,stack))
                from app.storage.db import init_db,get_connection,close_pool
                init_db(str(ROOT/'app/storage/schema.sql'));db=get_connection();c=Checks(db,output)
                stack.callback(close_pool);stack.callback(db.close)
                from app.services.parse_plan import preview,execute_plan
                from app.services.parse_selection import selection_scope,selection_from_plan
                from app.services import non_docent_parser as n
                from app.services.notification_eligibility import reconcile_notifications
                from tests.test_non_docent_extraction_safety import AWARD

                def sha(d):return c.scalar('SELECT v.sha256 FROM document_versions v JOIN documents d ON d.document_version_id=v.id WHERE d.id=%s',(d,))
                def plan(d):return preview(shas=[sha(d)],reprocess=True)
                def runs(d):return c.scalar('SELECT count(*) FROM document_parse_runs p JOIN documents d ON d.document_version_id=p.document_version_id WHERE d.id=%s',(d,))
                def run(text=AWARD):
                    with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[text]):
                        result=n.NonDocentParserService().parse_all_documents()
                    db.commit();return result
                def digest():
                    names=[r[0] for r in db.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
                    return {t:c.scalar('SELECT md5(coalesce(string_agg(to_jsonb(x)::text,\'|\' ORDER BY to_jsonb(x)::text),\'\')) FROM "'+t+'" x') for t in names}
                def awards(d):
                    return c.scalar("SELECT md5(coalesce(string_agg(to_jsonb(a)::text,'|' ORDER BY a.id),'')) FROM non_docent_awards a JOIN non_docent_publications p ON p.id=a.publication_id WHERE p.document_id=%s",(d,))

                first,_=c.doc();before=digest();db.commit()
                seen=[]
                original_factory=get_connection
                def readonly_factory():
                    connection=original_factory();original=connection.execute
                    def observed(sql,args=None):
                        result=original(sql,args)
                        if sql=='SET TRANSACTION READ ONLY':
                            seen.append(original('SHOW transaction_read_only').fetchone()[0])
                        return result
                    connection.execute=observed;return connection
                with patch('app.storage.db.get_connection',readonly_factory):ordinary=preview()
                c.check('preview_enforced_read_only_by_postgresql',seen==['on'])
                c.check('preview_changes_no_table',before==digest())
                c.check('new_live_selected',len(ordinary['entries'])==1)
                result=run();c.check('new_live_actual_parser_success',len(result)==1 and result[0]['status']=='success')
                c.check('ordinary_second_run_is_empty',run()==[] and runs(first)==1)
                reconcile_notifications();db.commit()
                c.check('new_live_can_create_validated_notice',c.scalar('SELECT queued_event_key IS NOT NULL FROM notification_document_state WHERE document_id=%s',(first,)))

                # Reproduce the former predicate against a genuine successful parse.
                with patch.object(n,'PARSER_VERSION',n.PARSER_VERSION+'.dr02'):
                    old_predicate=c.scalar("SELECT NOT EXISTS(SELECT 1 FROM document_parse_runs p JOIN documents d ON d.document_version_id=p.document_version_id WHERE d.id=%s AND p.parser_key=%s AND p.parser_version=%s AND p.status IN ('success','skipped'))",(first,n.PARSER_KEY,n.PARSER_VERSION))
                    c.check('former_version_predicate_reproduces_historical_selection',old_predicate)
                    c.check('new_version_does_not_execute_historical_success',run()==[] and runs(first)==1)

                outside,_=c.doc();run();outside_before=awards(outside)
                unknown,_=c.doc(family='unknown');historic,_=c.doc(intent='historical_import')
                correction,_=c.doc(url='https://example.invalid/1.pdf')
                c.check('unknown_historical_and_correction_not_automatic',run()==[])
                c.check('unknown_visible_as_held',any(x['document_id']==unknown for x in preview()['held']))
                for target in (unknown,):
                    try:plan(target)
                    except ValueError:c.check('unknown_cannot_be_authorized_by_parse_plan',True)
                    else:c.check('unknown_cannot_be_authorized_by_parse_plan',False)

                failed,_=c.doc();result=run('UNKNOWN FORMAT')
                c.check('failed_attempt_recorded',len(result)==1 and result[0]['status']=='failed')
                c.check('failed_has_no_automatic_retry',run()==[] and runs(failed)==1)
                skipped,_=c.doc();result=run('ADJUDICACION: No hay adjudicatarios.')
                c.check('legitimate_empty_skipped',len(result)==1 and result[0]['status']=='skipped')
                with patch.object(n,'PARSER_VERSION',n.PARSER_VERSION+'.dr02'):
                    c.check('skipped_does_not_retry_after_version_bump',run()==[] and runs(skipped)==1)

                replay=plan(first);before_award=awards(first)
                reconcile_notifications();db.commit()  # Drain earlier validated live fixtures into their own events.
                queues=c.scalar('SELECT count(*) FROM public_alerts')
                with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):
                    execution=execute_plan(replay,authorized=True)
                db.commit()
                c.check('explicit_sha_actual_dispatcher_reprocesses',sum(s.success_count for s in execution.parser_summaries)==1 and runs(first)==2)
                c.check('outside_sha_business_unchanged',awards(outside)==outside_before and runs(outside)==1)
                c.check('replay_sets_persistent_f07_hold',c.scalar('SELECT delivery_hold_reason FROM notification_document_state WHERE document_id=%s',(first,))=='explicit_reprocess')
                reconcile_notifications();db.commit()
                c.check('replay_no_new_publication_notice',c.scalar('SELECT count(*) FROM public_alerts')==queues)
                state=digest();db.commit()
                with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):
                    repeated=execute_plan(replay,authorized=True)
                db.commit()
                c.check('same_reviewed_plan_is_idempotent',all(s.documents_processed==0 for s in repeated.parser_summaries) and state==digest())

                failed_replay=plan(first);before_award=awards(first)
                with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=['UNKNOWN FORMAT']):
                    result=execute_plan(failed_replay,authorized=True)
                db.commit()
                c.check('explicit_failure_preserves_existing_data',awards(first)==before_award and sum(s.failed_count for s in result.parser_summaries)==1)
                c.check('failed_execution_retains_attempt_history',runs(first)==3)
                retry=plan(failed)
                with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):execute_plan(retry,authorized=True)
                db.commit();c.check('failed_retry_requires_and_accepts_new_plan',runs(failed)==2)

                from app.services import docent_bag_participants_parser as bag
                bag_doc,_=c.doc(family='docent_bag_participants')
                db.execute("UPDATE documents SET list_scope='maestros' WHERE id=%s",(bag_doc,));db.commit()
                text='CURSO 2026/2027\n1 SINTETIC, DOCENT AMB SERVEIS INF PRI'
                with patch.object(bag.DocentBagParticipantsParserService,'_extract_text',return_value=text):
                    result=bag.DocentBagParticipantsParserService().parse_all_documents()
                db.commit()
                c.check('docent_bag_real_selection_and_persistence',len(result)==1 and result[0]['status']=='success' and result[0]['rows_extracted']==1)
                with patch.object(bag,'PARSER_VERSION',bag.PARSER_VERSION+'.dr02'):
                    c.check('docent_bag_version_upgrade_no_automatic_replay',bag.DocentBagParticipantsParserService().parse_all_documents()==[])
                replay_bag=plan(bag_doc)
                with patch.object(bag.DocentBagParticipantsParserService,'_extract_text',side_effect=RuntimeError('synthetic_bag_failure')):
                    result=execute_plan(replay_bag,authorized=True)
                db.commit()
                c.check('docent_bag_failed_replay_keeps_members_and_attempt',sum(s.failed_count for s in result.parser_summaries)==1 and runs(bag_doc)==2 and c.scalar('SELECT count(*) FROM docent_bag_members')==1)
                c.check('docent_bag_replay_retention_persisted',c.scalar('SELECT delivery_hold_reason FROM notification_document_state WHERE document_id=%s',(bag_doc,))=='explicit_reprocess')

                # Actual default dispatcher entrypoint processes a new live item
                # with no authorization scope while every historical fixture stays held.
                scheduled,_=c.doc()
                with patch('sys.argv',['run_parse_documents.py']),patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]),redirect_stdout(io.StringIO()):
                    runpy.run_path(str(ROOT/'run_parse_documents.py'),run_name='__main__')
                db.commit();c.check('default_dispatcher_entry_processes_only_new_live',runs(scheduled)==1 and runs(first)==3 and runs(historic)==0)

                # A real second Python process executes the very same reviewed plan
                # while this process is paused after claim and before create_run.
                target,_=c.doc(intent='historical_import');concurrent_plan=plan(target)
                planfile=output/'concurrent_plan.json';planfile.write_text(json.dumps(concurrent_plan))
                marker=output/'owned_cluster.json';marker.write_text(json.dumps(dict(root=str(cluster.root),data=str(cluster.data),sock=str(cluster.sock),token=cluster.token)))
                ready=threading.Event();release=threading.Event();outcomes=[]
                real_locked=n.locked_documents
                def pause_claim(store,rows,key,version):
                    for doc in real_locked(store,rows,key,version):
                        ready.set()
                        if not release.wait(20):raise RuntimeError('synthetic_barrier_timeout')
                        yield doc
                def first_worker():
                    try:
                        with patch.object(n,'locked_documents',pause_claim),patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):
                            outcomes.append(execute_plan(concurrent_plan,authorized=True))
                    except BaseException as exc:outcomes.append(type(exc).__name__)
                thread=threading.Thread(target=first_worker);thread.start()
                try:
                    if not ready.wait(10):raise RuntimeError('first_worker_not_ready')
                    code='''import json,sys
from pathlib import Path
from contextlib import ExitStack
from tests import postgres_isolated_validation as isolated
info=json.loads(Path(sys.argv[1]).read_text())
owned=object.__new__(isolated.FreshCluster)
for k,v in info.items():setattr(owned,k,Path(v) if k!='token' else v)
with ExitStack() as stack:
 spies=list(isolated.connection_guards(owned,stack))
 from app.services.parse_plan import execute_plan
 result=execute_plan(json.loads(Path(sys.argv[2]).read_text()),authorized=True)
 assert all(s.documents_processed==0 for s in result.parser_summaries)
 assert all(spy.call_count==0 for _,spy in spies)
 print('second_process_did_not_process_locked_document')
'''
                    child=subprocess.run([sys.executable,'-B','-c',code,str(marker),str(planfile)],cwd=ROOT,env=dict(os.environ),capture_output=True,text=True,timeout=15)
                    (output/'second_process.log').write_text(child.stdout+child.stderr)
                    c.check('second_real_process_cannot_parse_claimed_document',child.returncode==0 and runs(target)==0)
                finally:
                    release.set();thread.join(10)
                db.commit()
                c.check('first_worker_completes_once',not thread.is_alive() and len(outcomes)==1 and not isinstance(outcomes[0],str) and runs(target)==1)

                # All individual entrypoints reject preview flags BEFORE a DB call.
                from app.storage import db as database
                for name in sorted(ROOT.glob('run_parse*.py')):
                    if name.name=='run_parse_selection.py':continue
                    with patch('sys.argv',[name.name,'--preview']),patch.object(database,'get_connection',side_effect=AssertionError('entrypoint_opened_database')),redirect_stdout(io.StringIO()),patch('sys.stderr',io.StringIO()):
                        try:runpy.run_path(str(name),run_name='__main__')
                        except SystemExit as exc:c.check('entry_rejects_ambiguous_preview_'+name.stem,exc.code==2)
                        else:c.check('entry_rejects_ambiguous_preview_'+name.stem,False)
                # The supported CLI really previews against PostgreSQL, with no writes.
                import run_parse_selection
                before=digest();db.commit()
                with redirect_stdout(io.StringIO()):status=run_parse_selection.main(['--preview'])
                db.commit();c.check('supported_cli_preview_read_only',status==0 and digest()==before)
                cli_doc,_=c.doc(intent='historical_import');cli_plan=plan(cli_doc)
                cli_file=output/'reviewed_cli_plan.json';cli_file.write_text(json.dumps(cli_plan))
                try:run_parse_selection.main(['--execute-plan',str(cli_file)])
                except ValueError:c.check('cli_execution_requires_authorization_flag',runs(cli_doc)==0)
                else:c.check('cli_execution_requires_authorization_flag',False)
                with patch.object(n.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]),redirect_stdout(io.StringIO()):
                    status=run_parse_selection.main(['--execute-plan',str(cli_file),'--authorize-reprocess'])
                    repeated=run_parse_selection.main(['--execute-plan',str(cli_file),'--authorize-reprocess'])
                db.commit();c.check('cli_reviewed_replay_executes_once',status==repeated==0 and runs(cli_doc)==1)
                for label,kwargs in [('family',{'family':'offered_positions'}),('parser',{'parser_key':'offered_positions_parser'})]:
                    try:preview(shas=[sha(cli_doc)],reprocess=True,**kwargs)
                    except ValueError:c.check('scope_filter_rejects_wrong_'+label,True)
                    else:c.check('scope_filter_rejects_wrong_'+label,False)
                c.check('both_shell_pipelines_use_protected_dispatcher',all('python run_parse_documents.py' in (ROOT/'scripts'/name).read_text() for name in ('run_pipeline.sh','run_remote_fetch_import_pipeline.sh')))
                c.check('no_real_transports',all(spy.call_count==0 for _,spy in spies))
                report.update(status='passed' if all(x['passed'] for x in c.checks) else 'failed',checks=c.checks)
        report['cluster_removed']=True
    except BaseException as exc:
        report.update(status='blocked_or_failed',error=isolated.safe_error(exc),checks=c.checks if c else [])
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output-dir',type=Path,required=True)
    raise SystemExit(main(parser.parse_args().output_dir))

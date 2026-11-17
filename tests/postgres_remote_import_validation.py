"""DR04 real importer/registry/parser, NEW owned PostgreSQL only; offline."""
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.postgres_notification_eligibility_validation import Checks, parse_document
from tests.test_remote_import_integrity import fixture


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
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0',
                       TELEGRAM_BOT_TOKEN='',TELEGRAM_CHANNEL_ID='',RADAR_PUSH_VAPID_PUBLIC_KEY='',
                       RADAR_PUSH_VAPID_PRIVATE_KEY='',INDEXNOW_KEY='',PGPASSFILE=str(cluster.root/'absent'),
                       NOTIFICATION_CUTOVER_UTC=(now-timedelta(hours=1)).isoformat())
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                    cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                spies=list(isolated.connection_guards(cluster,stack))
                from app.storage.db import init_db,get_connection,close_pool
                init_db(str(ROOT/'app/storage/schema.sql'));db=get_connection();c=Checks(db,output)
                stack.callback(close_pool);stack.callback(db.close)
                from scripts import import_remote_fetch as importer
                from app.services import remote_import_integrity as integrity
                from app.services.document_registry import DocumentRegistryService
                from app.services.notification_eligibility import reconcile_notifications
                from app.services.parse_plan import preview
                from app.services import non_docent_parser as parser
                from tests.document_fixtures import pdf_bytes
                from tests.test_non_docent_extraction_safety import AWARD
                incoming,manifest,item=fixture(cluster.root, pdf_bytes('DR04 historical'))
                item['asset']['asset_role']='non_docent_adc_award_pdf'
                item['asset']['publication_date_text']=now.strftime('%d/%m/%Y')
                path=incoming/'manifest.json';data=cluster.root/'data'
                def run(m=manifest,intent='historical_import'):
                    path.write_text(json.dumps(m))
                    with redirect_stdout(io.StringIO()):
                        return importer.import_manifest(manifest_path=path,incoming_dir=incoming,data_dir=data,notification_intent=intent)
                def digest():
                    tables=[r[0] for r in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
                    result={t:c.scalar('SELECT md5(coalesce(string_agg(to_jsonb(x)::text,\'|\' ORDER BY to_jsonb(x)::text),\'\')) FROM "'+t+'" x') for t in tables}
                    db.commit();return result
                def rejected(name,operation):
                    before=digest()
                    try:operation()
                    except integrity.ImportIntegrityError:
                        c.check(name+'_rejected',True)
                    else:c.check(name+'_rejected',False)
                    c.check(name+'_all_previous_tables_unchanged',before==digest())
                original_content=(incoming/'files/doc.pdf').read_bytes()
                result=run()
                c.check('valid_import_real_sha_size',result['new_versions_count']==1 and c.scalar('SELECT sha256 FROM document_versions')==item['sha256'] and c.scalar('SELECT size_bytes FROM document_versions')==len(original_content))
                registered=DocumentRegistryService().register_unclassified_documents(notification_intent='live_new')
                did=registered[0].document_id;vid=registered[0].document_version_id
                c.check('actual_classification_after_verified_import',registered[0].doc_family=='non_docent_adc_award')
                c.check('historical_intent_preserved_at_registration',c.scalar('SELECT intent FROM notification_document_state WHERE document_id=%s',(did,))=='historical_import')
                c.check('historical_not_automatically_selected',not preview()['entries'])
                c.check('explicit_historical_parse_succeeds',parse_document(c,did)[0]['status']=='success')
                reconcile_notifications();db.commit()
                c.check('historical_no_new_publication_notice',c.scalar('SELECT count(*) FROM public_alerts')==0 and c.scalar('SELECT count(*) FROM push_notification_events')==0)
                prior=digest();run(intent='live_new')
                c.check('reimport_known_hash_no_version_duplication',c.scalar('SELECT count(*) FROM document_versions')==1)
                c.check('reimport_does_not_promote_historical',c.scalar('SELECT notification_ingest_intent FROM document_versions WHERE id=%s',(vid,))=='historical_import' and c.scalar('SELECT intent FROM notification_document_state WHERE document_id=%s',(did,))=='historical_import')
                c.check('reimport_preserves_adjudications_and_parse_runs',all(digest()[t]==prior[t] for t in ('non_docent_awards','document_parse_runs','notification_document_state','notification_state_history')))
                with patch.object(parser.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]):
                    c.check('dr02_reimport_and_version_bump_do_not_reparse',parser.NonDocentParserService().parse_all_documents()==[])
                    with patch.object(parser,'PARSER_VERSION',parser.PARSER_VERSION+'.dr04'):
                        c.check('dr02_new_version_still_held',parser.NonDocentParserService().parse_all_documents()==[])
                for label,change in [('bad_sha',{'sha256':'0'*64}),('bad_size',{'size_bytes':len(original_content)+1}),
                                     ('unsafe_source',{'stored_filename':'../escape.pdf'}),('missing_file',{'relative_file_path':'files/absent.pdf'})]:
                    bad=copy.deepcopy(manifest);bad['sources'][0]['results'][0].update(change)
                    rejected(label,lambda b=bad:run(b))
                (incoming/'files/doc.pdf').write_bytes(original_content[:-1]);rejected('truncated_known_sha',run)
                (incoming/'files/doc.pdf').write_bytes(original_content)
                target=incoming/'files/doc.pdf';target.unlink();target.symlink_to(cluster.root/'absent')
                rejected('input_symlink',run);target.unlink();target.write_bytes(original_content)
                duplicate=copy.deepcopy(manifest);other=copy.deepcopy(item);other['sha256']='0'*64
                duplicate['sources'][0]['results'].append(other)
                rejected('conflicting_manifest',lambda:run(duplicate))
                duplicate['sources'][0]['results'][1]=copy.deepcopy(item)
                duplicate['sources'][0]['results'][1]['status']='same_run_duplicate'
                c.check('consistent_duplicates_reuse_existing_id',run(duplicate)['new_versions_count']==0 and c.scalar('SELECT count(*) FROM document_versions')==1)
                previous_target=data/'synthetic/files/doc.pdf'
                c.check('rejections_preserve_previous_file',previous_target.read_bytes()==original_content)

                # New version: swaps received path after preflight. The importer
                # must persist its pinned snapshot, never the replacement bytes.
                fresh_content=pdf_bytes('DR04 new live');target.write_bytes(fresh_content)
                fresh=copy.deepcopy(manifest);new=fresh['sources'][0]['results'][0]
                new.update(sha256=hashlib.sha256(fresh_content).hexdigest(),size_bytes=len(fresh_content),stored_filename='new.pdf')
                new['asset']['url']=new['asset']['canonical_url']='https://example.invalid/new.pdf'
                publish=importer.publish_snapshot
                def swapped(snapshot,**kwargs):
                    target.unlink();target.write_bytes(b'REPLACED')
                    return publish(snapshot,**kwargs)
                with patch.object(importer,'publish_snapshot',side_effect=swapped):run(fresh,intent='live_new')
                c.check('substitution_cannot_change_persisted_bytes',(data/'synthetic/files/new.pdf').read_bytes()==fresh_content)
                newdocs=DocumentRegistryService().register_unclassified_documents(notification_intent='live_new')
                c.check('new_live_selected_normally',len(newdocs)==1 and any(e['document_id']==newdocs[0].document_id for e in preview()['entries']))
                c.check('new_live_actual_parse',parse_document(c,newdocs[0].document_id)[0]['status']=='success')
                reconcile_notifications();db.commit()
                c.check('only_validated_live_document_can_queue',c.scalar('SELECT count(*) FROM public_alerts')==1)

                # Crash in second persistence operation after first INSERT: the
                # import transaction must roll back ALL earlier new rows too.
                crash=copy.deepcopy(manifest);crash_items=[]
                for number in (1,2):
                    content=pdf_bytes('DR04 crash '+str(number));name=f'crash{number}.pdf'
                    (incoming/'files'/name).write_bytes(content);entry=copy.deepcopy(item)
                    entry.update(sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content),stored_filename=name,relative_file_path='files/'+name)
                    entry['asset']['url']=entry['asset']['canonical_url']='https://example.invalid/'+name
                    crash_items.append(entry)
                crash['sources'][0]['results']=crash_items
                original_create=importer.SyncStore.create_document_version;calls=[]
                def crash_insert(store,**kwargs):
                    value=original_create(store,**kwargs);calls.append(value)
                    if len(calls)==2:raise RuntimeError('synthetic interruption after insert')
                    return value
                before=digest()
                try:
                    with patch.object(importer.SyncStore,'create_document_version',crash_insert):run(crash)
                except RuntimeError:pass
                c.check('database_interruption_rolls_back_whole_manifest',len(calls)==2 and before==digest())
                c.check('valid_orphans_kept_after_db_rollback',all((data/'synthetic/files'/x['stored_filename']).exists() for x in crash_items))
                c.check('retry_after_db_failure_idempotent',run(crash)['new_versions_count']==2 and run(crash)['new_versions_count']==0)
                c.check('retry_uses_original_historical_intent',c.scalar("SELECT count(*) FROM document_versions WHERE notification_ingest_intent='live_new'")==1)

                # Fresh entry, interruption before atomic link creates no rows
                # and leaves no half-published target or named staging file.
                copy_fail=copy.deepcopy(crash);copy_fail['sources'][0]['results']=copy_fail['sources'][0]['results'][:1]
                entry=copy_fail['sources'][0]['results'][0];content=pdf_bytes('DR04 copy interrupted')
                (incoming/'files/copy.pdf').write_bytes(content)
                entry.update(sha256=hashlib.sha256(content).hexdigest(),size_bytes=len(content),stored_filename='copy.pdf',relative_file_path='files/copy.pdf')
                entry['asset']['canonical_url']=entry['asset']['url']='https://example.invalid/copy.pdf'
                with patch.object(integrity.os,'link',side_effect=OSError('synthetic copy interruption')):
                    rejected('copy_interruption',lambda:run(copy_fail))
                c.check('no_partial_destination_after_copy_error',not (data/'synthetic/files/copy.pdf').exists() and not list(data.rglob('.import-*')))
                c.check('rejected_documents_have_no_detection_or_notice',c.scalar('SELECT count(*) FROM documents')==2 and c.scalar('SELECT count(*) FROM public_alerts')==1)
                c.check('no_real_external_transports',all(spy.call_count==0 for _,spy in spies))
                report.update(status='passed' if all(x['passed'] for x in c.checks) else 'failed',checks=c.checks,
                              counts={t:c.scalar('SELECT count(*) FROM '+t) for t in ('document_versions','documents','non_docent_awards','document_parse_runs','public_alerts')})
        report['cluster_removed']=True
    except BaseException as exc:
        report.update(status='blocked_or_failed',error=isolated.safe_error(exc),checks=c.checks if c else [])
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    raise SystemExit(main(p.parse_args().output_dir))

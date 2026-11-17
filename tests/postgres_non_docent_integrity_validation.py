"""Explicit F09 validation: owns a new PostgreSQL cluster, local PDFs/mocks only."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.test_non_docent_extraction_safety import AWARD,BAG,CALL

SPECS=[
 ('award','listadodefinitivo_adc_edu_174_26_firmado__ade5ea7c68a3.pdf','non_docent_adc_award','no_docente',1,None),
 ('call','adc_edu_188_26_firmado__c684b9b8b5f8.pdf','non_docent_adc_call','no_docente',1,None),
 ('ceice','listadobolsa_105_0726__9e56cc0f791b.pdf','non_docent_bag_update','no_docente',55,None),
 ('fp','79580-listadodefinitivo_733__b021b333aa2f.pdf','non_docent_funcion_publica_bag','no_docente',9,None),
 ('empty','listadodefinitivo_adc_edu_165_26_firmado__e10c2f00912e.pdf','non_docent_adc_award','no_docente',0,None),
 ('unsupported','listadodefinitivoadc_edu_pae_11_26_firmado__e73caeb92928.pdf','non_docent_adc_award','no_docente',0,None),
]


def bag_aware_semantics(v):
    result=v.semantic_snapshot()
    # The original nine-document runner had no bag members. Their generated
    # snapshot_id must be compared via ALL parent fields, not dropped blindly.
    result['non_docent_bag_members']=dict(v.query("""
        SELECT count(*) AS count,md5(coalesce(string_agg(payload,'|' ORDER BY payload),'')) AS digest
        FROM (SELECT jsonb_build_object(
            'snapshot',to_jsonb(s)-ARRAY['id','created_at'],
            'member',to_jsonb(m)-ARRAY['id','created_at','snapshot_id'])::text AS payload
            FROM non_docent_bag_members m JOIN non_docent_bag_snapshots s ON s.id=m.snapshot_id) x
    """)[0])
    return result


def exercise(v,manifest):
    from app.services import non_docent_parser as module
    from app.storage.non_docent_store import NonDocentStore
    v.import_and_register(manifest)
    service=module.NonDocentParserService()
    with v.replay_selection(parser_key=module.PARSER_KEY):
        results=service.parse_all_documents()
    by_name={r['original_filename']:r for r in results}
    # Original filenames differ from stored names; match actual registered IDs.
    docs={r['stored_filename']:r['id'] for r in v.query('SELECT d.id,v.stored_filename FROM documents d JOIN document_versions v ON v.id=d.document_version_id')}
    summaries={r['document_id']:r for r in results}
    for label,name,family,_,count,_ in SPECS:
        expected='skipped' if label=='empty' else 'failed' if label=='unsupported' else 'success'
        r=summaries[docs[name]]
        v.check('initial_'+label,r['status']==expected and r['rows_extracted']==count)
    v.check('f19_award_survives',v.scalar('SELECT count(*) FROM non_docent_awards WHERE position_code=\'491\'')==1)
    v.check('bag_members_persisted',v.scalar('SELECT count(*) FROM non_docent_bag_members')==64)
    before=v.snapshot();queues=v.snapshot(isolated.QUEUES)
    v.import_and_register(manifest)
    again=service.parse_all_documents()
    v.check('unsupported_requires_explicit_retry',again==[])
    v.check('second_import_exact_business_rows',before==v.snapshot())
    v.check('second_import_exact_queues',queues==v.snapshot(isolated.QUEUES))
    original_list=NonDocentStore.list_documents_for_parser
    for label,name,family,_,count,_ in SPECS[:4]:
        target=docs[name]
        def selected(store,original_list=original_list,target=target,**kw):
            return [r for r in original_list(store,**kw) if r['document_id']==target]
        empty=('ADJUDICACION EDU-1/26 Quedan desiertos: 12345 - CENTRO' if label=='award' else
               'CONVOCATORIA ADC: No hay puestos ofertados.' if label=='call' else
               'BOLSA: No hay personas integrantes.')
        partial=(AWARD+' No 1234567 - CENTRO' if label=='award' else
                 CALL+'\n456 A2 20 E OTRO CENTRO 100' if label=='call' else BAG+'\n2 ***5678** PERSONA SIN METRICAS')
        for stage,text,status in [('unknown','FORMATO NUEVO','failed'),('partial',partial,'failed'),('empty',empty,'skipped')]:
            with patch.object(module,'PARSER_VERSION',module.PARSER_VERSION+'.f09_'+label+'_'+stage),patch.object(NonDocentStore,'list_documents_for_parser',selected),patch.object(service,'_extract_pages_text',return_value=[text]),v.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                before=v.snapshot();queues=v.snapshot(isolated.QUEUES)
                runs=v.scalar('SELECT count(*) FROM document_parse_runs WHERE status=%s',(status,))
                result=service.parse_all_documents();v.db.commit()
                v.check(label+'_'+stage+'_status',len(result)==1 and result[0]['status']==status)
                v.check(label+'_'+stage+'_preserves_business',before==v.snapshot())
                v.check(label+'_'+stage+'_preserves_queues',queues==v.snapshot(isolated.QUEUES))
                v.check(label+'_'+stage+'_audit_persisted',v.scalar('SELECT count(*) FROM document_parse_runs WHERE status=%s',(status,))==runs+1)
                if status=='skipped':v.check(label+'_empty_not_selected_again',service.parse_all_documents()==[])
        method='insert_award' if label=='award' else 'insert_offered_position' if label=='call' else 'insert_bag_member'
        original_insert=getattr(NonDocentStore,method)
        def fail_after_insert(store,original_insert=original_insert,**kw):
            original_insert(store,**kw)
            raise RuntimeError('synthetic_failure_after_real_insert')
        with patch.object(module,'PARSER_VERSION',module.PARSER_VERSION+'.f09_write_'+label),patch.object(NonDocentStore,'list_documents_for_parser',selected):
            before=v.snapshot();semantic=bag_aware_semantics(v);queues=v.snapshot(isolated.QUEUES)
            runs=v.scalar("SELECT count(*) FROM document_parse_runs WHERE status='failed'")
            with patch.object(NonDocentStore,method,fail_after_insert),v.replay_selection(document_id=target,parser_key=module.PARSER_KEY):result=service.parse_all_documents()
            v.db.commit()
            v.check(label+'_pg02_after_insert',result[0]['status']=='failed' and v.scalar("SELECT count(*) FROM document_parse_runs WHERE status='failed'")==runs+1)
            v.check(label+'_after_insert_preserves_rows',before==v.snapshot())
            with v.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                retry=service.parse_all_documents();v.db.commit()
            v.check(label+'_valid_retry',len(retry)==1 and retry[0]['status']=='success')
            v.check(label+'_retry_semantics',semantic==bag_aware_semantics(v))
            v.check(label+'_retry_queues',queues==v.snapshot(isolated.QUEUES))
            v.check(label+'_retry_idempotent',service.parse_all_documents()==[])


def main(output):
    output=output.resolve()
    if not output.is_relative_to((ROOT/'auditoria_local/resultados').resolve()) or output.exists():
        raise ValueError('Need a new output directory within ignored results')
    output.mkdir(parents=True);isolated.OUT=output;isolated.ACTIVE_RUN=True
    report={'status':'running','existing_database_used':False,'tcp_enabled':False};v=None
    try:
        with isolated.FreshCluster() as cluster:
            (output/'owned_cluster.json').write_text(json.dumps({'root':str(cluster.root),'data':str(cluster.data),'socket':str(cluster.sock)})+'\n')
            env={k:x for k,x in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),PGPASSFILE=str(cluster.root/'absent'),ENABLE_NOTIFICATIONS='0',TELEGRAM_BOT_TOKEN='',TELEGRAM_CHANNEL_ID='',INDEXNOW_KEY='',RADAR_PUSH_VAPID_PUBLIC_KEY='',RADAR_PUSH_VAPID_PRIVATE_KEY='')
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                    cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
                spies=list(isolated.connection_guards(cluster,stack))
                from app.storage.db import init_db,close_pool
                init_db(str(ROOT/'app/storage/schema.sql'));v=isolated.Validation(cluster,report)
                with patch.object(isolated,'SPECS',SPECS):manifest,inputs=isolated.make_manifest(cluster.root)
                isolated.emit('six local documents and four-family failure/retry checks')
                exercise(v,manifest)
                v.check('no_notification_transports',all(spy.call_count==0 for _,spy in spies))
                v.check('original_pdfs_unchanged',all(isolated.sha(Path(p))==h for p,h in inputs.items()))
                report.update(checks=v.checks,findings=v.findings,status='failed' if v.findings else 'passed')
                close_pool();v.db.close()
        report['cluster_stopped_and_removed']=True
    except BaseException as exc:
        report.update(status='blocked_or_failed',error=isolated.safe_error(exc))
        if v:report.update(checks=v.checks,findings=v.findings)
    (output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(report['status'],flush=True)
    return 0 if report['status']=='passed' else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',type=Path,required=True)
    raise SystemExit(main(p.parse_args().output_dir))

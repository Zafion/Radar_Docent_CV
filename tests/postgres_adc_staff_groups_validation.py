"""PG-01 only: fresh PostgreSQL, two local ADC PDFs.

Explicit execution only. No DSN argument or existing database is accepted.
Reuses the isolated cluster and network/connection guards of the full runner.
"""
from __future__ import annotations
import asyncio
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import postgres_isolated_validation as isolated

OUT = ROOT / 'auditoria_local/resultados/postgresql_adc_groups'


async def api_check(validation, expected):
    import httpx
    from app.api.main import app
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated.invalid') as client:
        for group, count in expected.items():
            response = await client.get('/api/non-docent/awards', params={'staff_group_code': group})
            payload = response.json()
            validation.check('api_group_' + group, response.status_code == 200 and payload.get('total') == count
                             and all(row['staff_group_code'] == group for row in payload.get('items', [])),
                             {'expected_count': count, 'actual_count': payload.get('total')})


def validate_failure_recovery(validation, service):
    """Inject failures before parsing and after a real INSERT; retry each version."""
    from app.services import non_docent_parser as module
    from app.storage.non_docent_store import NonDocentStore
    target = validation.scalar('SELECT min(document_id) FROM non_docent_publications')
    original_list = NonDocentStore.list_documents_for_parser
    original_insert = NonDocentStore.insert_award

    def selected(store, **kwargs):
        return [row for row in original_list(store, **kwargs) if row['document_id'] == target]

    def insert_then_fail(store, **kwargs):
        original_insert(store, **kwargs)
        raise RuntimeError('synthetic_failure_after_real_insert')

    for stage in ('parse', 'after_write'):
        with patch.object(module, 'PARSER_VERSION', module.PARSER_VERSION + '.rollback_probe_' + stage), patch.object(
                NonDocentStore, 'list_documents_for_parser', selected):
            before = validation.snapshot()
            queues_before = validation.snapshot(isolated.QUEUES)
            failures_before = validation.scalar("SELECT count(*) FROM document_parse_runs WHERE status='failed'")
            if stage == 'parse':
                injection = patch.object(service, '_parse_document', side_effect=RuntimeError('synthetic_parse_failure'))
            else:
                injection = patch.object(NonDocentStore, 'insert_award', insert_then_fail)
            with injection, validation.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                summaries = service.parse_all_documents()
            validation.db.commit()
            validation.check(stage+'_failure_summary', len(summaries) == 1 and summaries[0]['status'] == 'failed')
            validation.check(stage+'_business_rows_unchanged', before == validation.snapshot())
            validation.check(stage+'_queues_unchanged', queues_before == validation.snapshot(isolated.QUEUES))
            validation.check(stage+'_failed_run_persisted', validation.scalar(
                "SELECT count(*) FROM document_parse_runs WHERE status='failed'") == failures_before+1)
            store = NonDocentStore()
            try:
                pending = store.list_documents_for_parser(doc_families=module.NON_DOCENT_DOC_FAMILIES,
                    parser_key=module.PARSER_KEY, parser_version=module.PARSER_VERSION)
            finally:
                store.close()
            validation.check(stage+'_failed_requires_explicit_retry', pending == [])
            before_counts = {key: value['count'] for key, value in before.items()}
            with validation.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                retry = service.parse_all_documents()
            validation.db.commit()
            validation.check(stage+'_retry_succeeds', len(retry) == 1 and retry[0]['status'] == 'success')
            validation.check(stage+'_retry_preserves_counts', before_counts == {
                key: value['count'] for key, value in validation.snapshot().items()})
            validation.check(stage+'_retry_has_no_pending', service.parse_all_documents() == [])
    asyncio.run(api_check(validation, {'TOC': 1, 'EEI': 1}))


def main(failure_cases=False):
    output = OUT.parent / 'postgresql_non_docent_failures' if failure_cases else OUT
    output.mkdir(parents=True, exist_ok=True)
    report = {'task': 'PG-02' if failure_cases else 'PG-01', 'status': 'running', 'checks': [], 'findings': [],
              'existing_database_used': False, 'tcp_enabled': False,
              'pdf_extraction_mocked': False}
    validation = None
    try:
        with isolated.FreshCluster() as cluster:
            env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'RADAR_', 'DATABASE_'))}
            env.update(PGPASSFILE=str(cluster.root / 'absent_password_file'), RADAR_DOCENT_DB_URL=cluster.dsn(),
                       ENABLE_NOTIFICATIONS='0', TELEGRAM_BOT_TOKEN='', TELEGRAM_CHANNEL_ID='',
                       RADAR_PUSH_VAPID_PUBLIC_KEY='', RADAR_PUSH_VAPID_PRIVATE_KEY='', INDEXNOW_KEY='')
            with patch.dict(os.environ, env, clear=True), ExitStack() as stack:
                import psycopg
                with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                    cluster.verify(admin)
                    admin.execute('CREATE DATABASE funk_validation')
                spies = list(isolated.connection_guards(cluster, stack))
                from app.storage.db import init_db, close_pool
                from app.services.non_docent_parser import NonDocentParserService, PARSER_VERSION
                init_db(str(ROOT / 'app/storage/schema.sql'))
                validation = isolated.Validation(cluster, report)
                therapy = ('adc_177', 'listadodefinitivo_adc_edu_177_27_firmado__ace8d7428118.pdf',
                           'non_docent_adc_award', 'no_docente', 1, None)
                with patch.object(isolated, 'SPECS', [isolated.SPECS[-1], therapy]):
                    manifest, originals = isolated.make_manifest(cluster.root)
                originals.update({str(p): isolated.sha(p) for p in (ROOT/'auditoria_local/metadata').glob('*.json')
                                  if p.name in {'manifest.json', 'state.json'}})
                validation.import_and_register(manifest)
                service = NonDocentParserService()
                from app.services.non_docent_parser import PARSER_KEY
                with validation.replay_selection(parser_key=PARSER_KEY):
                    summaries = service.parse_all_documents()
                validation.check('two_real_pdfs_one_award_each', len(summaries) == 2
                                 and all(r['status'] == 'success' and r['rows_extracted'] == 1 for r in summaries))
                groups = validation.query(
                    'SELECT v.stored_filename, g.code FROM non_docent_publications p '
                    'JOIN documents d ON d.id=p.document_id '
                    'JOIN document_versions v ON v.id=d.document_version_id '
                    'LEFT JOIN non_docent_staff_groups g ON g.id=p.staff_group_id ORDER BY v.stored_filename')
                validation.check('actual_publication_groups', groups == [
                    {'stored_filename': isolated.SPECS[-1][1], 'code': 'EEI'},
                    {'stored_filename': therapy[1], 'code': 'TOC'}], groups)
                bag_labels = validation.query('SELECT bag_name, position_code FROM non_docent_awards ORDER BY position_code')
                validation.check('adc_174_independent_bag_and_position_evidence', any(
                    'Educació Infantil' in row['bag_name'] and row['position_code'] == '491' for row in bag_labels))
                validation.check('adc_177_independent_bag_evidence', any(
                    'Teràpia Ocupacional' in row['bag_name'] for row in bag_labels))
                asyncio.run(api_check(validation, {'TOC': 1, 'EEI': 1}))
                before = validation.snapshot()
                validation.import_and_register(manifest)
                validation.check('second_import_no_pending', service.parse_all_documents() == [])
                validation.check('second_import_preserves_business_rows', before == validation.snapshot())
                if failure_cases:
                    validate_failure_recovery(validation, service)
                validation.check('parser_version_unchanged', PARSER_VERSION == '0.2.0')
                validation.check('original_inputs_unchanged', all(isolated.sha(Path(p)) == digest for p, digest in originals.items()))
                validation.check('no_external_notifications', all(spy.call_count == 0 for _, spy in spies))
                report['checks'], report['findings'] = validation.checks, validation.findings
                report['status'] = 'failed' if validation.findings else 'passed'
                close_pool(); validation.db.close()
        report['cluster_stopped_and_removed'] = True
    except BaseException as exc:
        report['status'], report['error'] = 'blocked_or_failed', isolated.safe_error(exc)
        if validation is not None:
            report['checks'], report['findings'] = validation.checks, validation.findings
        if isinstance(exc, KeyboardInterrupt):
            raise
    (output/'results.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(report['status'], flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())

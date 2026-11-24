"""Explicit integration runner. Owns a NEW PostgreSQL cluster; accepts no DSN.

Run from the repository: .venv/bin/python -B tests/postgres_isolated_validation.py
Requires existing PostgreSQL binaries/extensions and the ignored local PDFs.
Never uses SQLite, existing databases, deployment scripts or external transport.
"""
from __future__ import annotations
import asyncio
import argparse
from contextlib import ExitStack, redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import socket
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'auditoria_local/resultados/postgresql_validation'
ACTIVE_RUN = False
BIN = Path('/usr/lib/postgresql/16/bin')
PORT = 55439
SPECS = [
    ('maestros_interinos', 'ini_2026_adj_int_mae__229556fed06a.pdf', 'final_award_listing', 'maestros', 17051, 5116),
    ('maestros_carrera', 'ini_2026_adj_res_mae__2857d03b8443.pdf', 'final_award_listing', 'maestros', 119, 119),
    ('secundaria_interinos', 'ini_2026_adj_int_sec__c11e283b57a1.pdf', 'final_award_listing', 'secundaria_otros', 37534, 15127),
    ('secundaria_carrera', 'ini_2026_adj_rpp_sec__02152f7c87e9.pdf', 'final_award_listing', 'secundaria_otros', 304, 302),
    ('vacantes_inicio', 'lis_vac_act_adj_ini_26_27__5145b91d6fae.pdf', 'offered_positions', 'inicio_curso', 14226, None),
    ('plazas_junio', '260602_pue_def__4864e37de3c3.pdf', 'offered_positions', 'continua', 146, None),
    ('plazas_dc', '261030_pue_def__a426d3a64171.pdf', 'offered_positions', 'dificil_cobertura', 52, None),
    ('candidatos_dc', '261030_par__823d06203fa9.pdf', 'difficult_coverage_provisional', 'dificil_cobertura', 5381, 52),
    ('adc_174', 'listadodefinitivo_adc_edu_174_26_firmado__ade5ea7c68a3.pdf', 'non_docent_adc_award', 'no_docente', 1, None),
]
BUSINESS = ('document_versions', 'documents', 'award_results', 'award_assignments', 'offered_positions',
            'difficult_coverage_positions', 'difficult_coverage_candidates', 'non_docent_publications',
            'non_docent_offered_positions', 'non_docent_awards', 'non_docent_bag_snapshots', 'non_docent_bag_members')
QUEUES = ('public_alerts', 'notification_posts', 'push_notification_events')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def emit(stage):
    if ACTIVE_RUN:
        progress = {'stage': stage, 'updated_at': datetime.now(timezone.utc).isoformat()}
        temporary = OUT / 'progress.tmp'
        temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2) + '\n')
        temporary.replace(OUT / 'progress.json')
    print(f'[PostgreSQL aislado] {stage}', flush=True)


def safe_error(exc):
    # No SQL statements, bind values, person names or source document excerpts.
    result = {'type': type(exc).__name__, 'sqlstate': getattr(exc, 'sqlstate', None)}
    if str(exc).startswith(('required_local_fixture_missing_', 'fixture_classification_mismatch_', 'isolated_postgres_')):
        result['reason'] = str(exc)
    return result


class FreshCluster:
    def __enter__(self):
        for executable in ('initdb', 'pg_ctl', 'postgres'):
            if not (BIN / executable).is_file():
                raise RuntimeError('PostgreSQL temporary binaries unavailable')
        local_dir = ROOT / 'auditoria_local'
        local_dir.mkdir(mode=0o700, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='pgv_', dir=local_dir)
        self.root = Path(self.temp.name)
        self.data = self.root / 'd'; self.sock = self.root / 's'; self.sock.mkdir(mode=0o700)
        self.token = uuid.uuid4().hex
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'RADAR_', 'DATABASE_'))}
        self.started = False
        try:
            self.command('initdb', '-D', str(self.data), '--no-locale', '--encoding=UTF8',
                         '--username=funk_validation_owner', '--auth-local=trust', '--auth-host=reject', '--no-instructions')
            with (self.data / 'postgresql.conf').open('a') as handle:
                handle.write(f"\nlisten_addresses = ''\nport = {PORT}\nunix_socket_directories = '{self.sock}'\n"
                             "unix_socket_permissions = 0700\nmax_connections = 20\nshared_buffers = '32MB'\n"
                             "log_statement = 'none'\nlog_min_error_statement = 'panic'\nlog_error_verbosity = 'terse'\n"
                             f"funkcionario.validation_token = '{self.token}'\n")
            self.command('pg_ctl', '-D', str(self.data), '-l', str(self.root / 'server.log'), '-w', '-t', '20', 'start')
            self.started = True
            return self
        except BaseException:
            if (self.root / 'server.log').is_file():
                OUT.mkdir(parents=True, exist_ok=True)
                (OUT / 'startup_server_error.txt').write_text((self.root / 'server.log').read_text())
            self.__exit__(*sys.exc_info())
            raise

    def command(self, executable, *args):
        completed = subprocess.run([str(BIN / executable), *args], env=self.env, capture_output=True, text=True, timeout=40)
        if completed.returncode:
            # Startup logs contain no imported data and are useful if the sandbox blocks sockets.
            OUT.mkdir(parents=True, exist_ok=True)
            (OUT / 'startup_error.txt').write_text(completed.stdout + completed.stderr)
            raise RuntimeError(f'isolated_postgres_{executable}_failed')

    def __exit__(self, *args):
        if getattr(self, 'data', None) and (self.data / 'postmaster.pid').exists():
            self.command('pg_ctl', '-D', str(self.data), '-w', '-t', '20', '-m', 'immediate', 'stop')
        self.started = False
        if getattr(self, 'temp', None):
            self.temp.cleanup()

    def dsn(self, dbname='funk_validation'):
        from psycopg.conninfo import make_conninfo
        return make_conninfo(host=str(self.sock), port=PORT, user='funk_validation_owner',
                             dbname=dbname, connect_timeout=5, application_name='funk_isolated_validation')

    def guard_dsn(self, conninfo='', **kwargs):
        from psycopg.conninfo import conninfo_to_dict
        params = conninfo_to_dict(conninfo)
        params.update({key: kwargs[key] for key in ('host', 'port', 'user', 'dbname', 'service') if key in kwargs})
        if (params.get('host') != str(self.sock) or str(params.get('port')) != str(PORT)
                or params.get('dbname') not in {'postgres', 'funk_validation'} or params.get('service')
                or params.get('user') != 'funk_validation_owner'):
            raise AssertionError('database_target_outside_owned_cluster')

    def verify(self, connection):
        from psycopg.rows import tuple_row
        with connection.cursor(row_factory=tuple_row) as cursor:
            cursor.execute("SELECT current_setting('data_directory'), current_setting('funkcionario.validation_token'), current_setting('listen_addresses')")
            directory, token, listening = cursor.fetchone()
            if Path(directory).resolve() != self.data.resolve() or token != self.token or listening:
                connection.close()
                raise AssertionError('wrong_postgres_instance')
        connection.rollback()
        return connection


def connection_guards(cluster, stack):
    import psycopg
    original = psycopg.connect
    class_original = psycopg.Connection.connect
    def connect(conninfo='', **kwargs):
        cluster.guard_dsn(conninfo, **kwargs)
        return cluster.verify(original(conninfo, **kwargs))
    def class_connect(cls, conninfo='', **kwargs):
        cluster.guard_dsn(conninfo, **kwargs)
        return cluster.verify(class_original(conninfo, **kwargs))
    stack.enter_context(patch.object(psycopg, 'connect', connect))
    stack.enter_context(patch.object(psycopg.Connection, 'connect', classmethod(class_connect)))
    original_socket = socket.socket.connect
    def socket_connect(sock, address):
        if sock.family != socket.AF_UNIX or str(address) != str(cluster.sock / f'.s.PGSQL.{PORT}'):
            raise AssertionError('external_network_forbidden')
        return original_socket(sock, address)
    stack.enter_context(patch.object(socket.socket, 'connect', socket_connect))
    stack.enter_context(patch('socket.create_connection', side_effect=AssertionError('external_network_forbidden')))
    # Transport processes cannot inherit these socket mocks: force the offline
    # worker and reject any attempt to spawn the production worker in PG tests.
    stack.enter_context(patch('app.services.notification_transport.WORKER_MODULE','tests.notification_transport_stub'))
    original_popen=subprocess.Popen
    def guarded_popen(args,*a,**kw):
        if isinstance(args,(list,tuple)) and '--worker' in args and 'tests.notification_transport_stub' not in args:
            raise AssertionError('real_transport_process_forbidden')
        return original_popen(args,*a,**kw)
    stack.enter_context(patch.object(subprocess,'Popen',guarded_popen))
    # No real transport can run even if a notification gate regresses.
    for name in ('app.services.telegram_notifications._telegram_json_request',
                 'app.services.push_notifications.send_push_notification_to_all'):
        spy = stack.enter_context(patch(name, side_effect=AssertionError('external_notification_forbidden')))
        yield name, spy


class Validation:
    def __init__(self, cluster, report):
        self.cluster, self.report = cluster, report
        import psycopg
        from psycopg.rows import dict_row
        self.db = psycopg.connect(cluster.dsn(), row_factory=dict_row)
        self.cache = {}; self.checks = []; self.findings = []

    def query(self, sql, params=()):
        return self.db.execute(sql, params).fetchall()

    def scalar(self, sql, params=()):
        return next(iter(self.query(sql, params)[0].values()))

    def check(self, name, condition, details=None):
        self.checks.append({'name': name, 'passed': bool(condition), 'details': details})
        if not condition:
            self.findings.append({'check': name, 'severity': 'ALTO', 'details': details})
        if ACTIVE_RUN:
            temporary = OUT / 'live_checks.tmp'
            temporary.write_text(json.dumps({'checks': self.checks, 'findings': self.findings}, ensure_ascii=False, indent=2) + '\n')
            temporary.replace(OUT / 'live_checks.json')

    def check_pg03(self, stage):
        rows = self.query('''SELECT a.center_code, a.position_specialty_code
            FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
            WHERE r.document_id=%s AND a.position_code='915499' ''', (self.cache['secundaria_interinos_id'],))
        self.check('pg03_destination_' + stage, rows == [
            {'center_code': '03002007', 'position_specialty_code': '2A5'}], rows)

    def snapshot(self, tables=BUSINESS):
        result = {}
        for table in tables:
            row = self.query(f"SELECT count(*) AS count, md5(coalesce(string_agg(row_to_json(t)::text, '|' ORDER BY t.id), '')) AS digest FROM {table} t")[0]
            result[table] = dict(row)
        return result

    def semantic_snapshot(self):
        # Generated row IDs/timestamps may change under an explicit parser
        # version replay; preserve fields AND parent/destination associations.
        result = {}
        for table in BUSINESS:
            if table == 'documents':
                expression = "(to_jsonb(t) - ARRAY['created_at','parsed_at'])::text"
                source = 'documents t'
            elif table == 'award_assignments':
                expression = "jsonb_build_object('award',to_jsonb(r)-ARRAY['id','created_at'],'assignment',to_jsonb(t)-ARRAY['id','created_at','award_result_id','matched_offered_position_id'],'matched_offer',to_jsonb(o)-ARRAY['id','created_at','updated_at'])::text"
                source = 'award_assignments t JOIN award_results r ON r.id=t.award_result_id LEFT JOIN offered_positions o ON o.id=t.matched_offered_position_id'
            elif table == 'difficult_coverage_candidates':
                expression = "jsonb_build_object('position',to_jsonb(p)-ARRAY['id','created_at'],'candidate',to_jsonb(t)-ARRAY['id','created_at','position_id'])::text"
                source = 'difficult_coverage_candidates t JOIN difficult_coverage_positions p ON p.id=t.position_id'
            else:
                removed = ['id','created_at','updated_at']
                if table == 'non_docent_publications':
                    removed.append('notes')  # explicitly contains parser version
                quoted = ','.join("'" + item + "'" for item in removed)
                expression = f"(to_jsonb(t) - ARRAY[{quoted}])::text"
                source = table + ' t'
            result[table] = dict(self.query(f"SELECT count(*) AS count,md5(coalesce(string_agg(payload,'|' ORDER BY payload),'')) AS digest FROM (SELECT {expression} AS payload FROM {source}) semantic")[0])
        return result

    def services(self):
        from app.services import final_award_listing_maestros_parser as m
        from app.services import final_award_listing_secundaria_parser as s
        from app.services import offered_positions_parser as o
        from app.services import difficult_coverage_provisional_parser as d
        from app.services import non_docent_parser as n
        from app.storage.award_results_store import AwardResultsStore
        from app.storage.offered_positions_store import OfferedPositionsStore
        from app.storage.difficult_coverage_store import DifficultCoverageStore
        from app.storage.non_docent_store import NonDocentStore
        return [
            ('maestros', m, m.FinalAwardListingMaestrosParserService, AwardResultsStore, 'list_final_listing_documents', 'insert_award_result', 'maestros_carrera'),
            ('secundaria', s, s.FinalAwardListingSecundariaParserService, AwardResultsStore, 'list_final_listing_documents', 'insert_award_result', 'secundaria_carrera'),
            ('plazas', o, o.OfferedPositionsParserService, OfferedPositionsStore, 'list_offered_position_documents', 'insert_offered_position', 'vacantes_inicio'),
            ('dc', d, d.DifficultCoverageProvisionalParserService, DifficultCoverageStore, 'list_provisional_documents', 'insert_candidate', 'candidatos_dc'),
            ('no_docente', n, n.NonDocentParserService, NonDocentStore, 'list_documents_for_parser', 'insert_award', 'adc_174'),
        ]

    def pending(self, descriptor, version):
        kind, module, _, store_class, method, _, _ = descriptor
        store = store_class()
        try:
            kwargs = dict(parser_key=module.PARSER_KEY, parser_version=version)
            if kind in {'maestros', 'secundaria'}:
                kwargs['list_scope'] = 'maestros' if kind == 'maestros' else 'secundaria_otros'
            if kind == 'no_docente':
                kwargs['doc_families'] = module.NON_DOCENT_DOC_FAMILIES
            return getattr(store, method)(**kwargs)
        finally:
            store.close()

    def import_and_register(self, manifest):
        from scripts.import_remote_fetch import import_manifest
        with redirect_stdout(io.StringIO()):
            result = import_manifest(manifest_path=manifest, incoming_dir=manifest.parent, data_dir=self.cluster.root / 'files')
            registration = runpy.run_path(str(ROOT / 'run_register_documents.py'), run_name='__main__')
        return result, registration['registered']

    def replay_selection(self, *, document_id=None, parser_key=None):
        from app.services.parse_plan import preview
        from app.services.parse_selection import selection_from_plan, selection_scope
        sql='SELECT v.sha256 FROM document_versions v JOIN documents d ON d.document_version_id=v.id'
        digests=[row['sha256'] for row in self.query(sql+(' WHERE d.id=%s' if document_id else ''), (document_id,) if document_id else ())]
        if parser_key and not document_id:
            from app.services.parse_plan import parsers
            module,families,scope=next(x for x in parsers() if x[0].PARSER_KEY==parser_key)
            digests=[row['sha256'] for row in self.query(sql+' WHERE d.doc_family=ANY(%s)'+(' AND d.list_scope=%s' if scope else ''),(list(families),scope) if scope else (list(families),))]
        return selection_scope(selection_from_plan(preview(shas=digests,parser_key=parser_key,reprocess=True)))

    def seed_and_versions(self):
        # Durable synthetic history stays in place. New versions cannot silently
        # erase/reprocess it; explicit plans below are required for this fixture.
        for desc in self.services():
            kind, module, *_ = desc
            with self.replay_selection(parser_key=module.PARSER_KEY):
                docs=self.pending(desc,module.PARSER_VERSION)
            self.cache[kind+'_docs']=docs
            old_version={'maestros':'0.2.0','secundaria':'0.3.0','plazas':'0.4.0','dc':'0.3.0','no_docente':'0.2.0'}[kind]
            for doc in docs:
                self.db.execute("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at) VALUES (%s,%s,%s,'success',now())",(doc['document_version_id'],module.PARSER_KEY,old_version))
            self.db.commit()
            current=self.pending(desc,module.PARSER_VERSION)
            self.check('old_success_requires_explicit_plan_'+kind,not current,{'documents':len(docs),'selected':len(current),'old_version':old_version,'current_version':module.PARSER_VERSION})

    def parse(self):
        for desc in self.services():
            kind, module, service_class, *_ = desc
            emit(f'parsing completo: {kind}')
            service = service_class(); original = service._parse_document
            if kind != 'no_docente':
                def capture(doc, original=original):
                    result = original(doc); self.cache[doc['document_id']] = result; return result
                with self.replay_selection(parser_key=module.PARSER_KEY), patch.object(service, '_parse_document', side_effect=capture):
                    summaries = service.parse_all_documents()
            else:
                with self.replay_selection(parser_key=module.PARSER_KEY):
                    summaries = service.parse_all_documents()
            self.check(f'parse_{kind}', bool(summaries) and all(row['status'] == 'success' for row in summaries),
                       [{'filename': row['original_filename'], 'status': row['status'], 'rows': row.get('rows_extracted', row.get('candidates_extracted'))} for row in summaries])
        from app.services.assignment_matcher import AssignmentMatcherService
        emit('enlaces entre adjudicaciones y plazas')
        matches = AssignmentMatcherService().match_all()
        self.report['matching'] = {'processed': len(matches), 'linked': sum(row.matched_offered_position_id is not None for row in matches)}
        self.db.commit()

    def expected_counts(self):
        for label, filename, family, scope, expected, awarded in SPECS:
            doc = self.query('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.stored_filename=%s', (filename,))
            if not doc:
                self.check('registered_' + label, False); continue
            doc_id = doc[0]['id']; self.cache[label + '_id'] = doc_id
            if family == 'final_award_listing':
                count = self.scalar('SELECT count(*) FROM award_results WHERE document_id=%s', (doc_id,))
                selected = self.scalar("SELECT count(*) FROM award_results WHERE document_id=%s AND status='Adjudicat'", (doc_id,))
            elif family == 'offered_positions':
                count = self.scalar('SELECT count(*) FROM offered_positions WHERE document_id=%s', (doc_id,)); selected = None
            elif family == 'difficult_coverage_provisional':
                count = self.scalar('SELECT count(*) FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s', (doc_id,))
                selected = self.scalar('SELECT count(*) FROM difficult_coverage_positions WHERE document_id=%s', (doc_id,))
            else:
                count = self.scalar('SELECT count(*) FROM non_docent_awards a JOIN non_docent_publications p ON p.id=a.publication_id WHERE p.document_id=%s', (doc_id,)); selected = None
            self.check('persisted_' + label, count == expected and (awarded is None or selected == awarded), {'rows': count, 'expected': expected, 'awarded_or_positions': selected})
        missing = self.scalar("SELECT count(*) FROM award_results r LEFT JOIN award_assignments a ON a.award_result_id=r.id WHERE r.list_scope='secundaria_otros' AND r.status='Adjudicat' AND (a.id IS NULL OR a.center_code IS NULL OR a.position_code IS NULL)")
        self.report['secondary_incomplete_destinations'] = missing
        self.check('secondary_has_no_incomplete_destinations', missing == 0, {'count': missing})
        if missing:
            self.findings.append({'check': 'secondary_incomplete_destinations', 'severity': 'ALTO', 'count': missing, 'previously_reported': True})

        actual_group = self.scalar('SELECT g.code FROM non_docent_awards a JOIN non_docent_publications p ON p.id=a.publication_id LEFT JOIN non_docent_staff_groups g ON g.id=p.staff_group_id WHERE p.document_id=%s LIMIT 1', (self.cache['adc_174_id'],))
        self.check('adc_174_staff_group_matches_infant_education', actual_group == 'EEI', {'stored_group': actual_group, 'expected_group_from_document': 'EEI'})

    async def api_queries(self):
        import httpx
        from app.api.main import app
        import logging
        logging.getLogger('httpx').setLevel(logging.CRITICAL)
        teacher = self.scalar("SELECT person_name_normalized FROM award_results WHERE status='Adjudicat' ORDER BY id LIMIT 1")
        dc = self.scalar('SELECT id FROM difficult_coverage_positions ORDER BY id LIMIT 1')
        calls = [
            ('summary', '/api/docent/summary', {}),
            ('awards', '/api/awards', {'document_id': self.cache['maestros_interinos_id'], 'limit': 2}),
            ('offered', '/api/offered-positions', {'document_id': self.cache['vacantes_inicio_id'], 'include_unavailable': 'true', 'limit': 2}),
            ('dc', '/api/difficult-coverage/positions', {'limit': 2}),
            ('dc_candidates', f'/api/difficult-coverage/positions/{dc}/candidates', {'limit': 2}),
            ('non_docent', '/api/non-docent/awards', {'limit': 2}),
            ('person_profile', '/api/persons/profile', {'normalized_name': teacher}),
        ]
        results = []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url='http://isolated.invalid') as client:
            for label, url, params in calls:
                response = await client.get(url, params=params)
                self.check('api_' + label, response.status_code == 200, {'http_status': response.status_code})
                payload = response.json() if response.status_code == 200 else {}
                results.append({'endpoint': label, 'status': response.status_code, 'total': payload.get('total')})
                if label == 'awards' and response.status_code == 200:
                    self.check('api_awards_count', payload.get('total') == 17051 and len(payload['items']) == 2)
                if label == 'offered' and response.status_code == 200:
                    self.check('api_offered_count', payload.get('total') == 14226 and len(payload['items']) == 2)
        self.report['api'] = results

    def fault_and_reprocess(self):
        for desc in self.services():
            kind, module, service_class, store_class, method, insertion, label = desc
            target = self.cache.get(label + '_id')
            if target is None:
                continue
            original_list = getattr(store_class, method)
            def selected(store, *args, original_list=original_list, target=target, **kwargs):
                return [row for row in original_list(store, *args, **kwargs) if row['document_id'] == target]
            for stage in ('parse', 'after_write'):
                emit(f'rollback real: {kind}/{stage}')
                before = self.snapshot(); queue_before = self.snapshot(QUEUES)
                before_runs = self.scalar("SELECT count(*) FROM document_parse_runs WHERE status='failed'")
                service = service_class(); base_version = module.PARSER_VERSION
                with ExitStack() as stack:
                    stack.enter_context(patch.object(module, 'PARSER_VERSION', base_version + '.validation_' + stage))
                    stack.enter_context(patch.object(store_class, method, selected))
                    if stage == 'parse':
                        stack.enter_context(patch.object(service, '_parse_document', side_effect=RuntimeError('synthetic_parse_failure')))
                    else:
                        if kind != 'no_docente':
                            stack.enter_context(patch.object(service, '_parse_document', side_effect=lambda doc: self.cache[doc['document_id']]))
                        original_insert = getattr(store_class, insertion)
                        def failing_insert(store, *args, original_insert=original_insert, **kwargs):
                            original_insert(store, *args, **kwargs)
                            raise RuntimeError('synthetic_failure_after_real_insert')
                        stack.enter_context(patch.object(store_class, insertion, failing_insert))
                    with self.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                        summaries = service.parse_all_documents()
                self.db.commit()
                self.check(f'rollback_preserves_all_rows_{kind}_{stage}', before == self.snapshot())
                self.check(f'rollback_has_failed_summary_{kind}_{stage}', len(summaries) == 1 and summaries[0]['status'] == 'failed')
                self.check(f'rollback_preserves_queues_{kind}_{stage}', queue_before == self.snapshot(QUEUES))
                after_runs = self.scalar("SELECT count(*) FROM document_parse_runs WHERE status='failed'")
                self.check(f'failed_run_persisted_{kind}_{stage}', after_runs == before_runs + 1, {'before': before_runs, 'after': after_runs})
            # Actual selective reprocessing, new version, after fault tests.
            semantic_before = self.semantic_snapshot()
            queue_before_reprocess = self.snapshot(QUEUES)
            matched_before = self.scalar('SELECT count(*) FROM award_assignments WHERE matched_offered_position_id IS NOT NULL')
            counts_before = {table: value['count'] for table, value in self.snapshot().items()}
            with ExitStack() as stack:
                stack.enter_context(patch.object(module, 'PARSER_VERSION', module.PARSER_VERSION + '.validation_reprocess'))
                stack.enter_context(patch.object(store_class, method, selected))
                service = service_class()
                if kind != 'no_docente':
                    stack.enter_context(patch.object(service, '_parse_document', side_effect=lambda doc: self.cache[doc['document_id']]))
                with self.replay_selection(document_id=target,parser_key=module.PARSER_KEY):
                    summaries = service.parse_all_documents()
            self.db.commit()
            matched_without_refresh = self.scalar('SELECT count(*) FROM award_assignments WHERE matched_offered_position_id IS NOT NULL')
            from app.services.assignment_matcher import AssignmentMatcherService
            AssignmentMatcherService().match_all()
            self.db.commit()
            matched_after = self.scalar('SELECT count(*) FROM award_assignments WHERE matched_offered_position_id IS NOT NULL')
            self.report.setdefault('reprocess_matching', []).append({'parser':kind,'before':matched_before,'before_matcher_refresh':matched_without_refresh,'after':matched_after})
            self.check('selective_semantic_data_preserved_' + kind, semantic_before == self.semantic_snapshot())
            self.check('selective_no_new_notifications_' + kind, queue_before_reprocess == self.snapshot(QUEUES))
            self.check('selective_matching_restored_' + kind, matched_after == matched_before)
            self.check('selective_reprocess_' + kind, len(summaries) == 1 and summaries[0]['status'] == 'success')
            self.check('selective_counts_preserved_' + kind, counts_before == {table: value['count'] for table, value in self.snapshot().items()})
            self.check('current_version_has_no_pending_' + kind, not self.pending(desc, module.PARSER_VERSION))


def make_manifest(work):
    original = json.loads((ROOT / 'auditoria_local/metadata/manifest.json').read_text())
    results = [r for source in original['sources'] for r in source['results'] if r.get('stored_filename')]
    incoming = work / 'incoming'; (incoming / 'files').mkdir(parents=True)
    sources = []; inputs = {}
    from app.services.document_classifier import DocumentClassifierService
    classifier = DocumentClassifierService()
    for label, name, family, scope, *_ in SPECS:
        local = ROOT / 'auditoria_local/pdfs' / name
        if not local.exists():
            matches = list((ROOT / 'data').glob('*/files/' + name))
            if not matches:
                raise FileNotFoundError('required_local_fixture_missing_' + label)
            local = matches[0]
        selected = None
        for item in results:
            if item['stored_filename'] != name:
                continue
            asset = item['asset']
            result = classifier.classify(file_path=str(local), original_filename=item['original_filename'],
                asset_title=asset['title'], asset_role=asset['asset_role'], source_key=asset['source_key'],
                source_label='Isolated validation', section=asset.get('section'), publication_label=asset.get('publication_label'),
                publication_date_text=asset.get('publication_date_text'))
            if (result.doc_family, result.list_scope) == (family, scope):
                selected = json.loads(json.dumps(item)); break
        if selected is None and label == 'plazas_junio':
            # This historical fixture is in data/, outside the current capture.
            selected = {'asset': {'source_key':'validation_junio','source_url':'https://example.invalid/junio',
                'asset_role':'pdf_candidate','title':'Puestos ofertados', 'url':'https://example.invalid/260602_pue_def.pdf',
                'canonical_url':'https://example.invalid/260602_pue_def.pdf','section':'Adjudicaciones continuas',
                'publication_label':'Puestos ofertados','publication_date_text':'02/06/2026','downloadable':True},
                'sha256':sha(local),'original_filename':'260602_pue_def.pdf','stored_filename':name,
                'content_type':'application/pdf','size_bytes':local.stat().st_size,'downloaded_at':'2026-06-02T00:00:00+00:00'}
        if selected is None:
            raise RuntimeError('fixture_classification_mismatch_' + label)
        inputs[str(local)] = sha(local)
        shutil.copy2(local, incoming / 'files' / name)
        selected.update(status='downloaded', relative_file_path='files/' + name)
        asset = selected['asset']
        sources.append({'source_key':asset['source_key'],'source_url':asset['source_url'], 'source_label':'Isolated validation',
                        'status':'success','assets':[asset],'results':[selected]})
    manifest = incoming / 'manifest.json'; manifest.write_text(json.dumps({'schema_version':1,'sources':sources}))
    return manifest, inputs


def simulated_discovery(validation, manifest):
    """Synthetic HTML links must match the nine local manifest inputs, no HTTP."""
    from html import escape
    import httpx
    from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter
    from app.services.discovery.non_docent import NonDocentSubstitutionDiscoveryAdapter, SUBSTITUTION_SOURCE_PAGES
    sources = json.loads(manifest.read_text())['sources']
    adc = [s['assets'][0] for s in sources if s['source_key'].startswith('non_docent_')]
    teacher = [s['assets'][0] for s in sources if not s['source_key'].startswith('non_docent_')]
    adapters = [(RrhhMainDiscoveryAdapter(), teacher)]
    for asset in adc:
        page = next(p for p in SUBSTITUTION_SOURCE_PAGES if p.source_key == asset['source_key'])
        adapters.append((NonDocentSubstitutionDiscoveryAdapter(page), [asset]))
    discovered = set()
    for adapter, assets in adapters:
        html = '<h1>Adjudicaciones inicio de curso</h1>' + ''.join(
            '<a href="' + escape(a['url'], quote=True) + '">' + escape(a['title']) + '</a>' for a in assets)
        response = httpx.Response(200, text=html, headers={'content-type': 'text/html'}, request=httpx.Request('GET', adapter.source_url))
        with patch.object(adapter, '_fetch_response', return_value=response):
            discovered.update(a.canonical_url for a in adapter.discover_assets())
    expected = {s['assets'][0]['canonical_url'] for s in sources}
    validation.check('simulated_discovery_matches_local_manifest', discovered == expected,
                     {'discovered': len(discovered), 'expected': len(expected)})


def main(output_dir=None):
    global OUT, ACTIVE_RUN
    candidate = Path(output_dir) if output_dir else ROOT / 'auditoria_local/resultados' / (
        'postgresql_validation_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:6])
    candidate = candidate.resolve()
    if not candidate.is_relative_to((ROOT / 'auditoria_local/resultados').resolve()):
        raise ValueError('Output must remain under ignored audit results')
    if candidate.exists() and any(candidate.iterdir()):
        raise ValueError('Refusing to overwrite an existing validation run')
    OUT = candidate
    OUT.mkdir(parents=True, exist_ok=True)
    ACTIVE_RUN = True
    report = {'status':'running','postgresql':16,'existing_database_used':False,'tcp_enabled':False,
              'notification_environment':'0','checks':[],'findings':[]}
    validation = None
    original_inputs = {str(path):sha(path) for path in (ROOT/'auditoria_local/metadata').glob('*.json') if path.name in {'manifest.json','state.json'}}
    try:
        with FreshCluster() as cluster:
            report['cluster_started'] = True
            env = {key:value for key,value in os.environ.items() if not key.startswith(('PG','RADAR_', 'DATABASE_'))}
            env.update(PGPASSFILE=str(cluster.root / 'absent_password_file'), RADAR_DOCENT_DB_URL=cluster.dsn(), ENABLE_NOTIFICATIONS='0', TELEGRAM_BOT_TOKEN='', TELEGRAM_CHANNEL_ID='',
                       RADAR_PUSH_VAPID_PUBLIC_KEY='', RADAR_PUSH_VAPID_PRIVATE_KEY='', INDEXNOW_KEY='')
            with patch.dict(os.environ, env, clear=True), ExitStack() as stack:
                import psycopg
                # Create only a database in the newly initialized private cluster.
                cluster.guard_dsn(cluster.dsn('postgres'))
                with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                    cluster.verify(admin)
                    admin.execute('CREATE DATABASE funk_validation')
                transport_spies = list(connection_guards(cluster, stack))
                from app.storage.db import init_db, close_pool
                emit('aplicación del esquema actual en base nueva')
                init_db(str(ROOT/'app/storage/schema.sql'))
                validation = Validation(cluster, report)
                validation.check('private_cluster_identity', cluster.verify(validation.db) is validation.db)
                validation.check('required_extensions', {r['extname'] for r in validation.query("SELECT extname FROM pg_extension WHERE extname IN ('unaccent','pg_trgm')")} == {'unaccent','pg_trgm'})
                manifest, inputs = make_manifest(cluster.root); original_inputs.update(inputs)
                simulated_discovery(validation, manifest)
                emit('primera importación y clasificación con el registro real')
                imported, registered = validation.import_and_register(manifest)
                validation.check('nine_unique_documents_registered', len(registered)==len(SPECS), {'count':len(registered)})
                report['temporary_notification_records_before_parsing'] = {table: data['count'] for table,data in validation.snapshot(QUEUES).items()}
                validation.check('registration_creates_no_unvalidated_notifications', not any(report['temporary_notification_records_before_parsing'].values()))
                validation.seed_and_versions()
                validation.parse(); validation.expected_counts()
                validation.check_pg03('initial')
                emit('consulta de rutas FastAPI mediante ASGI sin servidor TCP')
                asyncio.run(validation.api_queries())
                emit('segunda importación, clasificación y selección de pendientes')
                before = validation.snapshot(); before_queues=validation.snapshot(QUEUES)
                imported2, registered2 = validation.import_and_register(manifest)
                validation.check('second_import_has_no_new_versions', imported2['new_versions_count']==0)
                validation.check('second_registry_has_no_new_documents', len(registered2)==0)
                for desc in validation.services():
                    validation.check('second_parse_no_pending_' + desc[0], not validation.pending(desc, desc[1].PARSER_VERSION))
                    validation.check('second_parse_no_work_' + desc[0], desc[2]().parse_all_documents()==[])
                validation.check('second_import_exact_business_snapshot', before==validation.snapshot())
                validation.check('second_import_no_extra_notifications', before_queues==validation.snapshot(QUEUES))
                report['append_only_import_history'] = {table:validation.scalar(f'SELECT count(*) FROM {table}') for table in ('sync_runs','assets')}
                validation.fault_and_reprocess()
                validation.check_pg03('after_selective_reprocessing')
                import run_send_notifications, run_publish_notifications
                queues=validation.snapshot(QUEUES)
                with redirect_stdout(io.StringIO()):
                    run_send_notifications.main(); run_publish_notifications.main()
                validation.check('disabled_publishers_leave_queue_untouched', queues==validation.snapshot(QUEUES))
                validation.check('no_external_notification_transport', all(spy.call_count==0 for _,spy in transport_spies))
                validation.check('original_files_unchanged', all(sha(Path(path))==digest for path,digest in original_inputs.items()))
                report['business_counts'] = {table:data['count'] for table,data in validation.snapshot().items()}
                report['versions']={desc[0]:desc[1].PARSER_VERSION for desc in validation.services()}
                reasons = {
                    'maestros_interinos':'if processed before ordinal fix / older parser',
                    'maestros_carrera':'reclassify existing unknown document before selective parse',
                    'secundaria_interinos':'select affected hash to recover PG-03 destination; require matcher refresh',
                    'secundaria_carrera':'reclassify existing unknown document before selective parse',
                    'vacantes_inicio':'field changes require manual hour review before replacing existing data',
                    'plazas_junio':'recover split codes and composition with targeted replay',
                    'plazas_dc':'repair specialty, hours and itinerancy fields',
                    'candidatos_dc':'recover missing positions/candidates; review optional metadata',
                    'adc_174':'same parser version does not select old success; replay only if affected by former three-digit position bug; EEI is correct',
                }
                report['selective_candidate_plan'] = [
                    {'label':label,'filename':filename,'sha256':next(digest for path,digest in original_inputs.items() if Path(path).name==filename),
                     'expected_rows':expected,'reason':reasons[label],'production_state_verified':False}
                    for label,filename,family,scope,expected,awarded in SPECS
                ]
                report['checks']=validation.checks;report['findings']=validation.findings
                report['status']='completed_with_findings' if validation.findings else 'passed'
                close_pool();validation.db.close()
        report['cluster_stopped_and_removed']=True
    except BaseException as exc:
        report['status']='blocked_or_failed';report['error']=safe_error(exc)
        if validation is not None:
            report['checks']=validation.checks;report['findings']=validation.findings
            try:
                from app.storage.db import close_pool
                close_pool();validation.db.close()
            except Exception:pass
        if isinstance(exc, KeyboardInterrupt):raise
    (OUT/'results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    emit(report['status'])
    return 0 if report['status']=='passed' else 2 if report['status']=='completed_with_findings' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='New empty directory under auditoria_local/resultados; never a database DSN')
    raise SystemExit(main(parser.parse_args().output_dir))

"""Operational rehearsal only: two NEW owned clusters, synthetic data, no transport.

Does not accept a DSN, run shell pipelines, or manage existing services.
"""
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests import postgres_isolated_validation as isolated
from tests.postgres_notification_eligibility_validation import Checks, phase_ingestion


def main(output, extension_schema='public', legacy_backup=False):
    output = output.resolve()
    if not output.is_relative_to(ROOT / 'auditoria_local/resultados') or output.exists():
        raise ValueError('new ignored output directory required')
    output.mkdir(parents=True)
    isolated.OUT = output
    report = {'status': 'running', 'existing_database_used': False, 'external_sends': 0}
    checks = []
    before = None
    backup = output / 'synthetic_legacy.dump'
    # Exact tracked base, not a guessed production schema.
    legacy = subprocess.check_output(['git', 'show', 'HEAD:app/storage/schema.sql'], cwd=ROOT, text=True)
    migration = (ROOT / 'app/storage/migrations/20261105_notification_eligibility.sql').read_text()
    normalization_migration = (ROOT / 'app/storage/migrations/20261106_normalize_text_restore.sql').read_text()
    report['unaccent_schema'] = extension_schema
    report['legacy_backup_recovery'] = legacy_backup
    restored_snapshot = None
    samples = [None, '', '  ÀÉÍÓÚ Ç Ñ  ', '  SINTÈTIC,   GÜELL\tL·L  ', 'ABC-123', '  A\nB  ']

    def normalization(db):
        return [db.execute('SELECT public.normalize_text(%s)', (value,)).fetchone()[0] for value in samples]

    def indexed_person_lookup(db):
        db.execute('SET LOCAL enable_seqscan=off')
        plan = db.execute("EXPLAIN (FORMAT JSON) SELECT id FROM public.award_results WHERE public.normalize_text(person_display_name)=public.normalize_text('sintetic c guell')").fetchone()[0]
        return 'idx_award_results_person_display_name_norm_trgm' in str(plan)

    def dump_backup(cluster):
        cluster.command('pg_dump', '--format=custom', '--file=' + str(backup), '--dbname=' + cluster.dsn())
        check('backup_created_and_catalog_readable', backup.stat().st_size > 0)
        cluster.command('pg_restore', '--list', str(backup))
        report['backup_sha256'] = hashlib.sha256(backup.read_bytes()).hexdigest()

    def snapshot(db):
        tables = db.execute("SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
        data = {row[0]: db.execute('SELECT md5(coalesce(string_agg(to_jsonb(t)::text,\'|\' ORDER BY to_jsonb(t)::text),\'\')) FROM public.' + '"' + row[0].replace('"', '""') + '" t').fetchone()[0] for row in tables}
        sequences = db.execute("SELECT sequencename,last_value FROM pg_catalog.pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall()
        indexes = db.execute("SELECT c.relname,pg_catalog.pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready FROM pg_catalog.pg_index i JOIN pg_catalog.pg_class c ON c.oid=i.indexrelid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname").fetchall()
        constraints = db.execute("SELECT r.relname,c.conname,pg_catalog.pg_get_constraintdef(c.oid),c.convalidated FROM pg_catalog.pg_constraint c JOIN pg_catalog.pg_class r ON r.oid=c.conrelid JOIN pg_catalog.pg_namespace n ON n.oid=r.relnamespace WHERE n.nspname='public' ORDER BY r.relname,c.conname").fetchall()
        functions = db.execute("SELECT p.proname,pg_catalog.pg_get_function_identity_arguments(p.oid),pg_catalog.pg_get_functiondef(p.oid) FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND p.prokind='f' ORDER BY 1,2").fetchall()
        return dict(data=data, sequences=[list(x.values()) for x in sequences], indexes=[list(x.values()) for x in indexes], constraints=[list(x.values()) for x in constraints], functions=[list(x.values()) for x in functions], normalization=normalization(db))

    def check(name, ok):
        checks.append({'name': name, 'passed': bool(ok)})
        (output / 'live_checks.json').write_text(json.dumps(checks, indent=2) + '\n')

    def fingerprints(db):
        return {table: db.execute(f"SELECT md5(coalesce(string_agg((to_jsonb(t)-'notification_ingest_intent')::text,'|' ORDER BY id),'')) FROM {table} t").fetchone()[0]
                for table in ('documents', 'document_versions', 'public_alerts', 'notification_posts', 'push_notification_events')}

    try:
        for stage in ('upgrade', 'restore'):
            with isolated.FreshCluster() as cluster:
                now = datetime.now(timezone.utc)
                env = {k: v for k, v in os.environ.items() if not k.startswith(('PG', 'RADAR_', 'DATABASE_', 'NOTIFICATION_'))}
                env.update(RADAR_DOCENT_DB_URL=cluster.dsn(), ENABLE_NOTIFICATIONS='0',
                           TELEGRAM_BOT_TOKEN='', TELEGRAM_CHANNEL_ID='', INDEXNOW_KEY='',
                           RADAR_PUSH_VAPID_PUBLIC_KEY='', RADAR_PUSH_VAPID_PRIVATE_KEY='',
                           PGPASSFILE=str(cluster.root / 'absent'),
                           NOTIFICATION_CUTOVER_UTC=(now - timedelta(hours=1)).isoformat())
                with patch.dict(os.environ, env, clear=True), ExitStack() as stack:
                    import psycopg
                    with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                        cluster.verify(admin)
                        admin.execute('CREATE DATABASE funk_validation')
                    spies = list(isolated.connection_guards(cluster, stack))
                    from app.storage.db import get_connection, close_pool
                    from app.services.notification_eligibility import reconcile_notifications
                    from app.storage.public_alert_store import enqueue_public_alert, list_public_alerts
                    from app.storage.push_event_store import enqueue_push_notification_event, list_pending_push_notification_events
                    db = get_connection()
                    stack.callback(close_pool)
                    stack.callback(db.close)
                    if stage == 'upgrade':
                        try:
                            db._conn.execute(normalization_migration)
                        except psycopg.errors.ObjectNotInPrerequisiteState:
                            db.rollback()
                            check('missing_extension_rejected_without_partial_function', db.execute("SELECT to_regprocedure('public.normalize_text(text)') IS NULL").fetchone()[0])
                            db.commit()
                        else:
                            raise AssertionError('missing extension not rejected')
                        # Exercise the actual install schema in an empty database,
                        # then roll back this test-only DDL before the legacy fixture.
                        if extension_schema != 'public':
                            from psycopg import sql
                            db._conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(extension_schema)))
                            db._conn.execute(sql.SQL('CREATE EXTENSION unaccent WITH SCHEMA {}').format(sql.Identifier(extension_schema)))
                        db._conn.execute((ROOT / 'app/storage/schema.sql').read_text())
                        check('fresh_current_schema_normalizes_with_restricted_path',
                              db.execute("SET LOCAL search_path=pg_catalog") is not None
                              and db.execute("SELECT public.normalize_text(' ÀÉ Ç Ñ ')").fetchone()[0] == 'ae c n')
                        db.rollback()
                        db.execute(legacy)
                        db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('legacy','https://example.invalid','synthetic')")
                        vid = db.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at) VALUES(%s,'legacy.pdf','legacy.pdf','synthetic.pdf',1,now()) RETURNING id", ('e' * 64,)).fetchone()[0]
                        db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,(SELECT id FROM sources WHERE source_key='legacy'),'unknown','synthetic')", (vid,))
                        db.execute("INSERT INTO award_results(document_id,list_scope,person_display_name,person_name_normalized,status,raw_block_text) VALUES((SELECT id FROM documents WHERE document_version_id=%s),'synthetic','SINTÈTIC Ç GÜELL','sintetic c guell','synthetic','synthetic')", (vid,))
                        enqueue_public_alert(db, event_key='legacy', event_type='synthetic', title='synthetic', summary='synthetic', public_url='/')
                        enqueue_push_notification_event(db, event_key='legacy', event_type='synthetic', title='synthetic', body='synthetic', url='/')
                        db.commit()
                        original_outputs = normalization(db)
                        original_oid = db.execute("SELECT p.oid,p.proowner,p.proacl FROM pg_catalog.pg_proc p WHERE p.oid='public.normalize_text(text)'::regprocedure").fetchone()
                        old_indexes = snapshot(db)['indexes']
                        if extension_schema != 'public':
                            from psycopg import sql
                            db._conn.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(extension_schema)))
                            db._conn.execute(sql.SQL('ALTER EXTENSION unaccent SET SCHEMA {}').format(sql.Identifier(extension_schema)))
                        if legacy_backup:
                            db.commit()
                            dump_backup(cluster)
                        db._conn.execute(normalization_migration)
                        db.commit()
                        check('migration_uses_actual_extension_namespace', db.execute("SELECT n.nspname FROM pg_catalog.pg_extension e JOIN pg_catalog.pg_namespace n ON n.oid=e.extnamespace WHERE e.extname='unaccent'").fetchone()[0] == extension_schema)
                        check('normalization_outputs_unchanged', normalization(db) == original_outputs)
                        check('function_identity_owner_acl_preserved', original_oid == db.execute("SELECT p.oid,p.proowner,p.proacl FROM pg_catalog.pg_proc p WHERE p.oid='public.normalize_text(text)'::regprocedure").fetchone())
                        check('no_existing_index_replaced_or_omitted', old_indexes == snapshot(db)['indexes'])
                        db.execute('SET LOCAL search_path=pg_catalog')
                        check('restricted_search_path_normalization', normalization(db) == original_outputs)
                        check('person_comparison_unchanged', db.execute("SELECT count(*) FROM public.award_results WHERE public.normalize_text(person_display_name)=public.normalize_text('sintetic c guell')").fetchone()[0] == 1)
                        check('existing_normalized_index_remains_usable', indexed_person_lookup(db))
                        db.commit()
                        definition = db.execute("SELECT pg_catalog.pg_get_functiondef('public.normalize_text(text)'::regprocedure)").fetchone()[0]
                        db._conn.execute(normalization_migration)
                        db.commit()
                        check('normalization_migration_idempotent', definition == db.execute("SELECT pg_catalog.pg_get_functiondef('public.normalize_text(text)'::regprocedure)").fetchone()[0])
                        before = fingerprints(db)
                        restored_snapshot = snapshot(db)
                        (output / 'backup_snapshot.json').write_text(json.dumps(restored_snapshot, indent=2, default=str) + '\n')
                        db.commit()
                        # No actual jobs are started: the orchestrator model must be
                        # stopped before DDL. Cannot certify a real server scheduler.
                        jobs = dict(producer=False, parser=False, matcher=False, web=False, telegram=False, push=False, timer=False)
                        check('simulated_all_jobs_quiesced', not any(jobs.values()))
                        if not legacy_backup:
                            dump_backup(cluster)
                        # A new web reader must not serve against the old schema.
                        try:
                            list_public_alerts(db)
                        except psycopg.errors.UndefinedTable:
                            db.rollback()
                            check('premature_reader_initialization_rejected', True)
                        else:
                            check('premature_reader_initialization_rejected', False)
                        try:
                            db.execute(migration)
                            db.execute('SELECT missing_readiness_column FROM public_alerts')
                        except psycopg.errors.UndefinedColumn:
                            db.rollback()
                        check('failed_initial_migration_has_no_partial_ddl', db.execute("SELECT to_regclass('notification_document_state') IS NULL").fetchone()[0])
                        check('failed_migration_preserves_legacy', fingerprints(db) == before)
                        db.commit()
                    else:
                        if legacy_backup:
                            # All archive sections are restored. Repair only the
                            # wrapper after pre-data, before any dependent index is
                            # rebuilt; never omit indexes/functions/constraints.
                            cluster.command('pg_restore', '--exit-on-error', '--single-transaction', '--section=pre-data', '--dbname=' + cluster.dsn(), str(backup))
                            db._conn.execute(normalization_migration)
                            db.commit()
                            check('legacy_backup_wrapper_repaired_before_indexes', True)
                            cluster.command('pg_restore', '--exit-on-error', '--single-transaction', '--section=data', '--section=post-data', '--dbname=' + cluster.dsn(), str(backup))
                        else:
                            cluster.command('pg_restore', '--exit-on-error', '--single-transaction', '--dbname=' + cluster.dsn(), str(backup))
                        check('backup_restored_in_second_new_cluster', fingerprints(db) == before)
                        actual = snapshot(db)
                        (output / 'restored_snapshot.json').write_text(json.dumps(actual, indent=2, default=str) + '\n')
                        for kind in restored_snapshot:
                            check('restored_' + kind + '_identical', actual[kind] == restored_snapshot[kind])
                        check('restored_all_indexes_valid', all(row[2] and row[3] for row in actual['indexes']))
                        db.execute('SET LOCAL search_path=pg_catalog')
                        check('restored_restricted_path_person_comparison', db.execute("SELECT count(*) FROM public.award_results WHERE public.normalize_text(person_display_name)=public.normalize_text('sintetic c guell')").fetchone()[0] == 1)
                        check('restored_normalized_index_is_usable', indexed_person_lookup(db))
                        db.commit()
                    # psycopg autocommit is false: all statements + DDL in ONE transaction.
                    db.execute(migration)
                    db.commit()
                    check(stage + '_migration_preserves_legacy', fingerprints(db) == before)
                    check(stage + '_six_tables', db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_name IN ('notification_document_state','notification_validation_attestations','notification_state_history','notification_delivery_attempts','notification_delivery_history','notification_legacy_holds')").fetchone()[0] == 6)
                    check(stage + '_two_history_triggers', db.execute("SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN ('notification_state_trace','notification_delivery_trace')").fetchone()[0] == 2)
                    check(stage + '_phase_index', db.execute("SELECT to_regclass('idx_notification_state_phase') IS NOT NULL").fetchone()[0])
                    check(stage + '_old_origin_unknown', db.execute("SELECT notification_ingest_intent FROM document_versions WHERE id=%s", (vid,)).fetchone()[0] == 'unknown')
                    reconcile_notifications()
                    db.commit()
                    check(stage + '_legacy_retained_not_rewritten', fingerprints(db) == before and db.execute('SELECT count(*) FROM notification_legacy_holds').fetchone()[0] == 2)
                    check(stage + '_compatible_readers_hide_legacy', list_public_alerts(db) == [] and list_pending_push_notification_events(db) == [])
                    if stage == 'upgrade':
                        c = Checks(db, output)
                        phase_ingestion(c)
                        for item in c.checks:
                            check(item['name'], item['passed'])
                        # Roll back all inserts from a reconciler failure after parsing.
                        from tests.postgres_notification_eligibility_validation import parse_document
                        doc, _ = c.doc()
                        parse_document(c, doc)
                        from app.storage import push_event_store
                        with patch.object(push_event_store, 'enqueue_push_notification_event', side_effect=RuntimeError('synthetic')):
                            try:
                                reconcile_notifications()
                            except RuntimeError:
                                pass
                            else:
                                raise AssertionError('fault injection not reached')
                        db.commit()
                        check('reconciliation_failure_has_no_half_outbox', db.execute('SELECT queued_event_key IS NULL FROM notification_document_state WHERE document_id=%s', (doc,)).fetchone()[0])
                        reconcile_notifications()
                        db.commit()
                        key = db.execute('SELECT queued_event_key FROM notification_document_state WHERE document_id=%s', (doc,)).fetchone()[0]
                        # Model a stalled provider while holding the real delivery lock.
                        from app.storage.notification_eligibility_store import lock_delivery_eligibility, processing_started
                        check('delivery_can_lock_validated_document', lock_delivery_eligibility(db, key))
                        other = get_connection()
                        try:
                            other.execute("SET LOCAL lock_timeout='100ms'")
                            version = db.execute('SELECT document_version_id FROM documents WHERE id=%s', (doc,)).fetchone()[0]
                            try:
                                processing_started(other, version)
                            except psycopg.errors.LockNotAvailable:
                                other.rollback()
                                check('stalled_delivery_blocks_same_document_parser', True)
                            else:
                                other.rollback()
                                check('stalled_delivery_blocks_same_document_parser', False)
                            check('unrelated_document_update_is_not_blocked', bool(other.execute("UPDATE notification_document_state SET updated_at=now() WHERE document_id<>%s RETURNING document_id", (doc,)).fetchall()))
                            other.rollback()
                            db.rollback()
                            other.execute("SET LOCAL lock_timeout='100ms'")
                            processing_started(other, version)
                            other.rollback()
                            check('parser_recovers_after_delivery_lock_release', True)
                        finally:
                            other.close()
                    check(stage + '_no_real_transports', all(spy.call_count == 0 for _, spy in spies))
            check(stage + '_cluster_removed', not cluster.root.exists())
        report.update(status='passed' if all(c['passed'] for c in checks) else 'failed', checks=checks)
    except BaseException as exc:
        report.update(status='blocked_or_failed', error=isolated.safe_error(exc), checks=checks)
    (output / 'results.json').write_text(json.dumps(report, indent=2) + '\n')
    print(report['status'], len(checks), flush=True)
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--extension-schema', default='public', help='Synthetic extension namespace inside the NEW cluster only.')
    parser.add_argument('--legacy-backup', action='store_true', help='Back up the defective old wrapper and rehearse recovery through every archive section.')
    args = parser.parse_args()
    raise SystemExit(main(args.output_dir, args.extension_schema, args.legacy_backup))

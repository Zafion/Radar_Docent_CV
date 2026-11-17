"""End-to-end synthetic test on a new local PostgreSQL cluster only."""
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from tests.postgres_isolated_validation import FreshCluster, ROOT, connection_guards
from tests.document_fixtures import pdf_bytes
from tests.test_non_docent_extraction_safety import AWARD
from tests.selective_synthetic_runner import run_fixture


class FullSyntheticFlow(unittest.TestCase):
    def test_selective_flow_replay_and_rejection(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                cluster.verify(admin)
                admin.execute('CREATE DATABASE funk_validation')
            env = {k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(), ENABLE_NOTIFICATIONS='0')
            with patch.dict(os.environ,env,clear=True), ExitStack() as stack:
                list(connection_guards(cluster, stack))
                from app.storage.db import init_db, close_pool
                stack.callback(close_pool)
                init_db(str(ROOT/'app/storage/schema.sql'))
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as db:
                    sid=db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('old','https://example.invalid','old') RETURNING id").fetchone()['id']
                    vid=db.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,'old','old','/absent',1,now(),'unknown') RETURNING id",('f'*64,)).fetchone()['id']
                    old_id=db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,title) VALUES(%s,%s,'unknown','old') RETURNING id",(vid,sid)).fetchone()['id']
                stage = cluster.root/'staging'; stage.mkdir()
                payload = pdf_bytes('synthetic'); (stage/'synthetic.pdf').write_bytes(payload)
                url='https://example.invalid/synthetic.pdf'
                manifest={'format':'funkcionario.selective-technical.v2','entries':[{
                    'identity':url,'file':'synthetic.pdf','sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload),
                    'asset':{'source_key':'synthetic_fixture','source_url':'https://example.invalid',
                    'url':url,'canonical_url':url,'asset_role':'non_docent_adc_award_pdf',
                    'title':'Adjudicación definitiva ADC',
                    'publication_date_text':datetime.now(timezone.utc).strftime('%d/%m/%Y')}}]}
                raw=json.dumps(manifest).encode(); digest=hashlib.sha256(raw).hexdigest()
                from app.services import non_docent_parser as parser
                stack.enter_context(patch.object(parser.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]))
                stack.enter_context(patch('app.services.assignment_matcher.AssignmentMatcherService.match_all',side_effect=AssertionError('matcher')))
                stack.enter_context(patch('app.services.notification_eligibility.reconcile_if_configured',side_effect=AssertionError('reconcile')))
                with patch('app.storage.sync_store.SyncStore.create_asset',side_effect=RuntimeError('synthetic_failure')):
                    with self.assertRaisesRegex(RuntimeError,'synthetic_failure'):
                        run_fixture(cluster,stage,raw,digest)
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as check:
                    self.assertEqual(check.execute("SELECT count(*) AS n FROM sources WHERE source_key='synthetic_fixture'").fetchone()['n'],0)
                    self.assertEqual(check.execute('SELECT count(*) AS n FROM documents WHERE id=%s',(old_id,)).fetchone()['n'],1)
                first=run_fixture(cluster,stage,raw,digest)
                self.assertEqual(first['processed'],1)
                self.assertEqual(first['failed'],0)
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as db:
                    def counts():
                        return [db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in
                            ('sources','sync_runs','assets','document_versions','documents','document_parse_runs',
                             'public_alerts','notification_posts','push_notification_events')]
                    before=counts()
                    names=[row['tablename'] for row in db.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()]
                    def snapshot():
                        from psycopg import sql
                        return {name: db.execute(sql.SQL("SELECT md5(coalesce(string_agg(to_jsonb(t)::text, '|' ORDER BY to_jsonb(t)::text), '')) AS digest FROM {} t").format(sql.Identifier(name))).fetchone()['digest'] for name in names}
                    all_before=snapshot()
                    self.assertEqual(before[-3:],[0,0,0])
                    old_before=db.execute('SELECT to_jsonb(d)::text AS row FROM documents d WHERE id=%s',(old_id,)).fetchone()['row']
                    again=run_fixture(cluster,stage,raw,digest)
                    db.rollback()
                    self.assertEqual(again['processed'],0)
                    self.assertEqual(counts(),before)
                    self.assertEqual(snapshot(),all_before)
                    with self.assertRaises(ValueError): run_fixture(cluster,stage,raw,'0'*64)
                    db.rollback(); self.assertEqual(counts(),before)
                    self.assertEqual(db.execute('SELECT to_jsonb(d)::text AS row FROM documents d WHERE id=%s',(old_id,)).fetchone()['row'],old_before)


if __name__ == '__main__':
    unittest.main()

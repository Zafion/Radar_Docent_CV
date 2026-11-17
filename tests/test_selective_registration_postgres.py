"""Focused synthetic PostgreSQL 16 registration test; no production connections."""
import hashlib
import unittest
from unittest.mock import Mock, patch

import psycopg
from psycopg.rows import dict_row

from app.services.document_registry import DocumentRegistryService
from app.storage.db import PgCompatConnection
from app.storage.document_store import DocumentStore
from tests.postgres_isolated_validation import FreshCluster, ROOT


class RegistrationPostgresTests(unittest.TestCase):
    def test_selected_registration_and_rollback(self):
        with FreshCluster() as cluster:
            conn = psycopg.connect(cluster.dsn('postgres'), row_factory=dict_row)
            conn.execute((ROOT / 'app/storage/schema.sql').read_text())
            fixture = cluster.root / 'synthetic.pdf'
            fixture.write_bytes(b'synthetic document')
            sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
            source = conn.execute("INSERT INTO sources(source_key,source_url,label) VALUES('test','https://example.invalid','test') RETURNING id").fetchone()['id']
            run = conn.execute("INSERT INTO sync_runs(source_id,started_at,status) VALUES(%s,now(),'success') RETURNING id", (source,)).fetchone()['id']
            version = conn.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,'a.pdf','a.pdf',%s,18,now(),'live_new') RETURNING id", (sha, str(fixture))).fetchone()['id']
            asset = conn.execute("INSERT INTO assets(source_id,sync_run_id,asset_role,title,url,canonical_url,document_version_id) VALUES(%s,%s,'document','test','https://example.invalid/a','https://example.invalid/a',%s) RETURNING id", (source,run,version)).fetchone()['id']
            fixture2 = cluster.root / 'other.pdf'
            fixture2.write_bytes(b'other synthetic')
            sha2 = hashlib.sha256(fixture2.read_bytes()).hexdigest()
            version2 = conn.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,'b.pdf','b.pdf',%s,15,now(),'live_new') RETURNING id", (sha2,str(fixture2))).fetchone()['id']
            asset2 = conn.execute("INSERT INTO assets(source_id,sync_run_id,asset_role,title,url,canonical_url,document_version_id) VALUES(%s,%s,'document','test','https://example.invalid/b','https://example.invalid/b',%s) RETURNING id", (source,run,version2)).fetchone()['id']
            conn.commit()
            # The service owns and closes its connection; open a new local socket only.
            conn.close()
            raw = psycopg.connect(cluster.dsn('postgres'), row_factory=dict_row)
            store = DocumentStore(PgCompatConnection(raw))
            svc = DocumentRegistryService()
            svc.classifier = Mock()
            svc.classifier.classify.return_value = Mock(doc_family='offered_positions', title='test', document_date_text=None, document_date_iso=None, list_scope=None, classifier_version='test')
            entry = dict(asset_id=asset,document_version_id=version,sha256=sha,canonical_url='https://example.invalid/a',source_key='test')
            other = dict(asset_id=asset2,document_version_id=version2,sha256=sha2,canonical_url='https://example.invalid/wrong',source_key='test')
            with patch('app.services.document_registry.DocumentStore', return_value=store):
                with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                    svc.register_selected_live([entry, other])
            with psycopg.connect(cluster.dsn('postgres'), row_factory=dict_row) as check:
                self.assertEqual(check.execute('SELECT count(*) AS n FROM documents').fetchone()['n'], 0)
            raw = psycopg.connect(cluster.dsn('postgres'), row_factory=dict_row)
            store = DocumentStore(PgCompatConnection(raw))
            with patch('app.services.document_registry.DocumentStore', return_value=store):
                self.assertEqual(len(svc.register_selected_live([entry])), 1)
            with psycopg.connect(cluster.dsn('postgres'), row_factory=dict_row) as check:
                self.assertEqual(check.execute('SELECT count(*) AS n FROM documents').fetchone()['n'], 1)
                self.assertEqual(check.execute('SELECT count(*) AS n FROM documents WHERE document_version_id=%s',(version2,)).fetchone()['n'], 0)
                for table in ('public_alerts','notification_posts','push_notification_events'):
                    self.assertEqual(check.execute('SELECT count(*) AS n FROM ' + table).fetchone()['n'], 0)


if __name__ == '__main__':
    unittest.main()

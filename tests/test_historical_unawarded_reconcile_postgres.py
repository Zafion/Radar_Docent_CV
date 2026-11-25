"""Exercise the limited unawarded-result adapter in a synthetic cluster."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from app.services.historical_unawarded_reconcile import reconcile_unawarded
from tests.postgres_isolated_validation import FreshCluster

SCHEMA='''CREATE TABLE document_versions(id bigserial PRIMARY KEY,sha256 text UNIQUE NOT NULL);
CREATE TABLE documents(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id));
CREATE TABLE award_results(id bigserial PRIMARY KEY,document_id bigint REFERENCES documents(id),order_number integer,
 status text,raw_block_text text,person_display_name text,person_name_normalized text,specialty_code text,list_scope text);
CREATE TABLE award_assignments(id bigserial PRIMARY KEY,award_result_id bigint REFERENCES award_results(id));
CREATE TABLE public_alerts(id int PRIMARY KEY);CREATE TABLE notification_posts(id int PRIMARY KEY);CREATE TABLE push_notification_events(id int PRIMARY KEY);'''


class UnawardedAdapterTests(unittest.TestCase):
    def test_independent_conflict_idempotence_and_failure_rollback(self):
        with FreshCluster() as cluster, tempfile.TemporaryDirectory() as temp:
            pdf=Path(temp)/'fixture.pdf';pdf.write_bytes(b'synthetic pdf');sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as source, psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db in (source,dst):
                    db.execute(SCHEMA)
                    for table in ('public_alerts','notification_posts','push_notification_events'):db.execute('INSERT INTO '+table+' VALUES(1)')
                    db.commit()
                source.execute('INSERT INTO document_versions(id,sha256) VALUES(30,%s)',(sha,))
                source.execute('INSERT INTO documents(id,document_version_id) VALUES(40,30)')
                for i,status in ((1,'No adjudicat'),(2,'No adjudicat'),(3,'Adjudicat')):
                    source.execute('''INSERT INTO award_results(id,document_id,order_number,status,raw_block_text,
                      person_display_name,person_name_normalized,specialty_code,list_scope) VALUES(%s,40,%s,%s,%s,%s,%s,'001','maestros')''',
                      (100+i,i,status,f'block-{i}',f'fixture-{i}',f'FIXTURE-{i}'))
                source.execute('INSERT INTO award_assignments(award_result_id) VALUES(103)');source.commit()
                dst.execute('INSERT INTO document_versions(id,sha256) VALUES(60,%s)',(sha,))
                dst.execute('INSERT INTO documents(id,document_version_id) VALUES(70,60)')
                dst.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope) VALUES(70,1,'No adjudicat','different','fixture-1','FIXTURE-1','001','maestros')")
                dst.commit()
                parsed=[SimpleNamespace(order_number=i,status=status,raw_block_text=f'block-{i}',person_display_name=f'fixture-{i}',person_name_normalized=f'FIXTURE-{i}',specialty_code='001') for i,status in ((1,'No adjudicat'),(2,'No adjudicat'),(3,'Adjudicat'))]
                def run():
                    with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as parser:
                        parser.return_value._parse_document.return_value=parsed
                        return reconcile_unawarded(source,dst,document_sha=sha,pdf_path=pdf,family='maestros',synthetic_token=cluster.token)
                dst.execute("CREATE FUNCTION fail_unawarded() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
                dst.execute('CREATE TRIGGER fail_unawarded BEFORE INSERT ON award_results FOR EACH ROW EXECUTE FUNCTION fail_unawarded()');dst.commit()
                with self.assertRaises(psycopg.Error):run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],1);dst.commit()
                dst.execute('DROP TRIGGER fail_unawarded ON award_results');dst.commit()
                first=run();self.assertEqual(first.get('inserted'),1);self.assertEqual(first.get('quarantined'),1)
                again=run();self.assertEqual(again.get('inserted',0),0)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_unawarded.quarantine').fetchone()['n'],1)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],2)
                for table in ('public_alerts','notification_posts','push_notification_events'):
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],1)


if __name__=='__main__':unittest.main()

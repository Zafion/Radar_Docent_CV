"""Complete expert procedure adapter with synthetic isolated PostgreSQL data."""
import hashlib
import unittest
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from app.services.historical_expert_reconcile import reconcile_expert_procedure
from app.services.unawarded_expert_vacancies_parser import UnawardedExpertVacancy
from tests.postgres_isolated_validation import FreshCluster

PDF = b'%PDF-1.4\nsynthetic fixture\n%%EOF\n'
SHA = hashlib.sha256(PDF).hexdigest()
URL = 'https://ceice.gva.es/documents/synthetic.pdf'
PAGE = 'https://ceice.gva.es/es/web/rrhh-educacion/fixture'
HTML = ('<a href="' + URL + '">fixture</a>').encode()
HTML_SHA = hashlib.sha256(HTML).hexdigest()
KEY = 'expert-fixture'
SCHEMA = '''
CREATE TABLE sources(id bigserial PRIMARY KEY,source_key text UNIQUE,source_url text);
CREATE TABLE document_versions(id bigserial PRIMARY KEY,sha256 text UNIQUE,notification_ingest_intent text);
CREATE TABLE document_parse_runs(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id),status text,rows_extracted int,finished_at timestamptz);
CREATE TABLE documents(id bigserial PRIMARY KEY,document_version_id bigint UNIQUE REFERENCES document_versions(id),source_id bigint REFERENCES sources(id),doc_family text,
  validation_status text,validation_parse_run_id bigint REFERENCES document_parse_runs(id),historical_status text,publication_date_quality text);
CREATE TABLE historical_procedures(procedure_key text PRIMARY KEY,procedure_kind text,procedure_date date,date_evidence text,publication_date date,publication_date_quality text);
CREATE TABLE historical_procedure_documents(procedure_key text REFERENCES historical_procedures(procedure_key),document_id bigint REFERENCES documents(id),document_role text,relationship_evidence text,PRIMARY KEY(procedure_key,document_id));
CREATE TABLE historical_unawarded_expert_vacancies(document_id bigint REFERENCES documents(id),center_code text,source_position_id text,province text,locality text,center_name text,cycle text,modules text,teaching_hours text,total_hours text,valencian_source text,result_status text,PRIMARY KEY(document_id,center_code,source_position_id));
CREATE TABLE public_alerts(id int PRIMARY KEY); CREATE TABLE notification_posts(id int PRIMARY KEY); CREATE TABLE push_notification_events(id int PRIMARY KEY);
'''


class ExpertAdapterTests(unittest.TestCase):
    def test_empty_partial_complete_conflict_and_rollback(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                admin.execute('CREATE DATABASE funk_validation')
                admin.execute('CREATE DATABASE source_fixture')
            with psycopg.connect(cluster.dsn('source_fixture'), row_factory=dict_row) as src, psycopg.connect(cluster.dsn(), row_factory=dict_row) as dst:
                for db in (src, dst):
                    db.execute(SCHEMA)
                    db.execute("INSERT INTO sources(id,source_key,source_url) VALUES(8,'rrhh_main','https://ceice.gva.es/es/web/rrhh-educacion')")
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        db.execute('INSERT INTO '+table+' VALUES(1)')
                    db.commit()
                src.execute("INSERT INTO document_versions(id,sha256,notification_ingest_intent) VALUES(80,%s,'live_new')", (SHA,))
                src.execute("INSERT INTO document_parse_runs(id,document_version_id,status,rows_extracted) VALUES(81,80,'success',6)")
                src.execute("INSERT INTO documents(id,document_version_id,source_id,doc_family) VALUES(82,80,8,'expert')")
                src.execute("INSERT INTO historical_procedures VALUES(%s,'expert','2026-11-05','fixture',NULL,'unknown')", (KEY,))
                src.execute("INSERT INTO historical_procedure_documents VALUES(%s,82,'vacancy','fixture')", (KEY,))
                for i in range(6):
                    src.execute("INSERT INTO historical_unawarded_expert_vacancies VALUES(82,%s,%s,'p','l','center','cycle','module','h','h','n','unawarded_no_requests')", (f'c{i}', f'p{i}'))
                src.commit()
                def run():
                    rows=[UnawardedExpertVacancy(f'c{i}',f'p{i}','p','l','center','cycle','module','h','h','n') for i in range(6)]
                    with patch('app.services.historical_expert_reconcile.parse_expert_pdf',return_value=('2026-11-05',rows)):
                        return reconcile_expert_procedure(src,dst,sha256=SHA,html_bytes=HTML,html_sha256=HTML_SHA,page_url=PAGE,
                                                          document_url=URL,pdf_bytes=PDF,procedure_key=KEY,synthetic_token=cluster.token)
                for existing in (0, 3, 6):
                    dst.execute('TRUNCATE historical_unawarded_expert_vacancies,historical_procedure_documents,historical_procedures,documents,document_parse_runs,document_versions RESTART IDENTITY CASCADE')
                    if existing:
                        dst.execute("INSERT INTO document_versions(id,sha256,notification_ingest_intent) VALUES(900,%s,'unknown')", (SHA,))
                        dst.execute("INSERT INTO documents(id,document_version_id,source_id,doc_family) VALUES(901,900,8,'expert')")
                        for i in range(existing):
                            dst.execute("INSERT INTO historical_unawarded_expert_vacancies VALUES(901,%s,%s,'p','l','center','cycle','module','h','h','n','unawarded_no_requests')", (f'c{i}', f'p{i}'))
                    dst.commit()
                    first=run(); self.assertEqual(first.get('vacancies_inserted',0),6-existing)
                    self.assertEqual(first.get('vacancies_reused',0),existing)
                    second=run(); self.assertEqual(second.get('vacancies_inserted',0),0)
                    self.assertEqual(second.get('vacancies_reused'),6)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_procedures').fetchone()['n'],1)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies').fetchone()['n'],6)
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],1)
                    dst.commit()
                dst.execute("UPDATE historical_unawarded_expert_vacancies SET locality='different' WHERE center_code='c0'")
                dst.commit()
                with self.assertRaisesRegex(ValueError,'vacancy_fields_conflict'): run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies').fetchone()['n'],6)
                dst.commit()
                dst.execute("UPDATE historical_unawarded_expert_vacancies SET locality='l' WHERE center_code='c0'")
                dst.execute("INSERT INTO sources(id,source_key,source_url) VALUES(9,'rrhh_novedades','https://ceice.gva.es/es/web/rrhh-educacion/novedades')")
                dst.execute("UPDATE documents SET doc_family='ignored',source_id=9 WHERE document_version_id=900")
                dst.commit()
                self.assertEqual(run().get('vacancies_reused'),6)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies').fetchone()['n'],6)
                self.assertEqual(dst.execute('SELECT doc_family,source_id FROM documents WHERE document_version_id=900').fetchone(),{'doc_family':'ignored','source_id':9})
                dst.commit()
                wrong=[UnawardedExpertVacancy('wrong','p0','p','l','center','cycle','module','h','h','n')]
                with patch('app.services.historical_expert_reconcile.parse_expert_pdf',return_value=('2026-11-05',wrong)):
                    with self.assertRaisesRegex(ValueError,'source_vacancies_pdf_mismatch'):
                        reconcile_expert_procedure(src,dst,sha256=SHA,html_bytes=HTML,html_sha256=HTML_SHA,page_url=PAGE,
                                                   document_url=URL,pdf_bytes=PDF,procedure_key=KEY,synthetic_token=cluster.token)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies').fetchone()['n'],6)
                dst.commit()
                dst.execute('TRUNCATE historical_unawarded_expert_vacancies,historical_procedure_documents,historical_procedures,documents,document_parse_runs,document_versions RESTART IDENTITY CASCADE')
                dst.execute("CREATE FUNCTION fail_test() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
                dst.execute('CREATE TRIGGER fail_test BEFORE INSERT ON historical_unawarded_expert_vacancies FOR EACH ROW EXECUTE FUNCTION fail_test()')
                dst.commit()
                with self.assertRaises(psycopg.Error): run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM documents').fetchone()['n'],0)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_procedures').fetchone()['n'],0)


if __name__ == '__main__': unittest.main()

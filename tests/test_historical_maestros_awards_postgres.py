"""Synthetic integration tests for award results and verified offer links."""
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

SCHEMA='''CREATE TABLE sources(id bigserial PRIMARY KEY,source_key text UNIQUE,source_url text);
CREATE TABLE document_versions(id bigserial PRIMARY KEY,sha256 text UNIQUE NOT NULL,notification_ingest_intent text);
CREATE TABLE document_parse_runs(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id),status text,rows_extracted int,finished_at timestamptz);
CREATE TABLE documents(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id),source_id bigint REFERENCES sources(id),
 validation_status text,validation_parse_run_id bigint REFERENCES document_parse_runs(id),historical_status text,publication_date_quality text);
CREATE TABLE offered_positions(id bigserial PRIMARY KEY,document_id bigint REFERENCES documents(id),position_code text,center_code text,specialty_code text,body_code text);
CREATE TABLE award_results(id bigserial PRIMARY KEY,document_id bigint REFERENCES documents(id),order_number integer,status text,raw_block_text text,person_display_name text,person_name_normalized text,specialty_code text,list_scope text);
CREATE TABLE award_assignments(id bigserial PRIMARY KEY,award_result_id bigint REFERENCES award_results(id),assignment_kind text,locality text,center_code text,center_name text,position_specialty_code text,position_specialty_name text,position_code text,hours_text text,hours_value float8,petition_text text,petition_number integer,request_type text,raw_assignment_text text,matched_offered_position_id bigint REFERENCES offered_positions(id));
CREATE TABLE public_alerts(id int PRIMARY KEY);CREATE TABLE notification_posts(id int PRIMARY KEY);CREATE TABLE push_notification_events(id int PRIMARY KEY);'''
FIELDS=('assignment_kind','locality','center_code','center_name','position_specialty_code','position_specialty_name','position_code','hours_text','hours_value','petition_text','petition_number','request_type','raw_assignment_text')


class MaestrosAwardTests(unittest.TestCase):
    def test_quarantine_is_scoped_by_document_sha(self):
        with FreshCluster() as cluster,tempfile.TemporaryDirectory() as temp:
            pdf1=Path(temp)/'first.pdf';pdf1.write_bytes(b'first fixture');sha1=hashlib.sha256(pdf1.read_bytes()).hexdigest()
            pdf2=Path(temp)/'second.pdf';pdf2.write_bytes(b'second fixture');sha2=hashlib.sha256(pdf2.read_bytes()).hexdigest()
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src,psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db in (src,dst):
                    db.execute(SCHEMA)
                    db.execute('INSERT INTO document_versions(id,sha256) VALUES(10,%s),(11,%s)',(sha1,sha2))
                    db.execute('INSERT INTO documents(id,document_version_id) VALUES(20,10),(21,11)')
                    db.commit()
                for did in (20,21):
                    src.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope) VALUES(%s,99,'No adjudicat','source','fixture','FIXTURE','001','maestros')",(did,))
                src.commit()
                dst.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope) VALUES(20,99,'No adjudicat','different','fixture','FIXTURE','001','maestros')");dst.commit()
                parsed=[SimpleNamespace(order_number=99,status='No adjudicat',raw_block_text='source',person_display_name='fixture',person_name_normalized='FIXTURE',specialty_code='001',assignment=None)]
                with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as parser:
                    parser.return_value._parse_document.return_value=parsed
                    first=reconcile_unawarded(src,dst,document_sha=sha1,pdf_path=pdf1,family='maestros',synthetic_token=cluster.token,include_awards=True)
                    second=reconcile_unawarded(src,dst,document_sha=sha2,pdf_path=pdf2,family='maestros',synthetic_token=cluster.token,include_awards=True)
                self.assertEqual(first['quarantined'],1);self.assertEqual(second['inserted'],1)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_unawarded.quarantine WHERE document_sha=%s',(sha1,)).fetchone()['n'],1)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_unawarded.quarantine WHERE document_sha=%s',(sha2,)).fetchone()['n'],0)

    def test_partial_conflict_existing_award_link_idempotence_and_rollback(self):
        with FreshCluster() as cluster,tempfile.TemporaryDirectory() as temp:
            pdf=Path(temp)/'document.pdf';pdf.write_bytes(b'fixture');sha=hashlib.sha256(pdf.read_bytes()).hexdigest();offer_sha='f'*64
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src,psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db in (src,dst):
                    db.execute(SCHEMA)
                    db.execute("INSERT INTO sources(source_key,source_url) VALUES('rrhh_main','https://ceice.gva.es/es/web/rrhh-educacion')")
                    for t in ('public_alerts','notification_posts','push_notification_events'):db.execute('INSERT INTO '+t+' VALUES(1)')
                    db.commit()
                def docs(db,vid,did,oid):
                    db.execute('INSERT INTO document_versions(id,sha256) VALUES(%s,%s),(%s,%s)',(vid,sha,vid+1,offer_sha))
                    db.execute('INSERT INTO documents(id,document_version_id) VALUES(%s,%s),(%s,%s)',(did,vid,did+1,vid+1))
                    db.execute("INSERT INTO offered_positions(id,document_id,position_code,center_code,specialty_code,body_code) VALUES(%s,%s,'P','C','001','A')",(oid,did+1))
                docs(src,10,20,30);docs(dst,100,200,300)
                src.execute("UPDATE document_versions SET notification_ingest_intent='live_new' WHERE id=10")
                src.execute("INSERT INTO document_parse_runs(document_version_id,status,rows_extracted) VALUES(10,'success',3)")
                assignment=dict(zip(FIELDS,('type','town','C','center','001','specialty','P','h',1.0,'req',1,'type','assignment')))
                parsed=[]
                for i in (1,2,3):
                    status='Adjudicat' if i!=2 else 'No adjudicat'
                    src.execute('''INSERT INTO award_results(id,document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope)
                        VALUES(%s,20,%s,%s,%s,%s,%s,'001','maestros')''',(40+i,i,status,f'b{i}',f'person{i}',f'PERSON{i}'))
                    parsed.append(SimpleNamespace(order_number=i,status=status,raw_block_text=f'b{i}',person_display_name=f'person{i}',person_name_normalized=f'PERSON{i}',specialty_code='001',assignment=SimpleNamespace(**assignment) if i!=2 else None))
                    if i!=2:
                        src.execute('''INSERT INTO award_assignments(award_result_id,assignment_kind,locality,center_code,center_name,position_specialty_code,position_specialty_name,position_code,hours_text,hours_value,petition_text,petition_number,request_type,raw_assignment_text,matched_offered_position_id)
                          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,30)''',(40+i,*assignment.values()))
                src.commit()
                for ident,i,raw in ((401,1,'b1'),(402,2,'changed')):
                    status='Adjudicat' if i==1 else 'No adjudicat'
                    dst.execute('''INSERT INTO award_results(id,document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope)
                        VALUES(%s,200,%s,%s,%s,%s,%s,'001','maestros')''',(ident,i,status,raw,f'person{i}',f'PERSON{i}'))
                dst.execute('''INSERT INTO award_assignments(award_result_id,assignment_kind,locality,center_code,center_name,position_specialty_code,position_specialty_name,position_code,hours_text,hours_value,petition_text,petition_number,request_type,raw_assignment_text)
                    VALUES(401,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',tuple(assignment.values()))
                dst.commit()
                def run(offer_evidence=offer_sha):
                    with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as parser:
                        parser.return_value._parse_document.return_value=parsed
                        return reconcile_unawarded(src,dst,document_sha=sha,pdf_path=pdf,family='maestros',synthetic_token=cluster.token,include_awards=True,accredited_offer_sha=offer_evidence)
                dst.execute("CREATE FUNCTION fail_assignment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
                dst.execute('CREATE TRIGGER fail_assignment BEFORE INSERT ON award_assignments FOR EACH ROW EXECUTE FUNCTION fail_assignment()');dst.commit()
                with self.assertRaises(psycopg.Error):run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],2)
                self.assertIsNone(dst.execute('SELECT matched_offered_position_id FROM award_assignments').fetchone()['matched_offered_position_id'])
                self.assertIsNone(dst.execute("SELECT to_regnamespace('recovery_unawarded') AS n").fetchone()['n']);dst.commit()
                dst.execute('DROP TRIGGER fail_assignment ON award_assignments');dst.commit()
                first=run();self.assertEqual(first['inserted'],1);self.assertEqual(first['quarantined'],1)
                self.assertEqual(first['awards_inserted'],1);self.assertEqual(first['awards_reused'],1);self.assertEqual(first['associations_inserted'],2)
                second=run();self.assertEqual(second.get('inserted',0),0);self.assertEqual(second.get('awards_inserted',0),0);self.assertEqual(second.get('associations_inserted',0),0)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments WHERE matched_offered_position_id=300').fetchone()['n'],2)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_unawarded.quarantine').fetchone()['n'],1)
                for t in ('public_alerts','notification_posts','push_notification_events'):self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'],1)
                dst.execute('DELETE FROM award_assignments');dst.execute('DELETE FROM award_results')
                dst.execute('DELETE FROM recovery_unawarded.quarantine');dst.commit()
                no_evidence=run(None)
                self.assertEqual(no_evidence['inserted'],3);self.assertEqual(no_evidence['awards_inserted'],2)
                self.assertEqual(no_evidence.get('associations_inserted',0),0)
                self.assertEqual(no_evidence['associations_quarantined'],2)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments WHERE matched_offered_position_id IS NOT NULL').fetchone()['n'],0);dst.commit()
                with_evidence=run()
                self.assertEqual(with_evidence.get('inserted',0),0);self.assertEqual(with_evidence.get('awards_inserted',0),0)
                self.assertEqual(with_evidence['associations_inserted'],2)
                complete=run()
                self.assertEqual(complete.get('inserted',0),0);self.assertEqual(complete.get('associations_inserted',0),0)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],3)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments').fetchone()['n'],2)
                dst.execute('DELETE FROM award_assignments');dst.execute('DELETE FROM award_results')
                dst.execute('DELETE FROM recovery_unawarded.quarantine');dst.execute('DELETE FROM documents WHERE id=200')
                dst.execute('DELETE FROM document_versions WHERE id=100');dst.commit()
                document_url='https://ceice.gva.es/documents/fixture.pdf'
                html=('<a href="'+document_url+'">fixture</a>').encode()
                provenance={'html_bytes':html,'html_sha256':hashlib.sha256(html).hexdigest(),
                            'page_url':'https://ceice.gva.es/es/fixture','document_url':document_url}
                with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as parser:
                    parser.return_value._parse_document.return_value=parsed
                    empty=reconcile_unawarded(src,dst,document_sha=sha,pdf_path=pdf,family='maestros',synthetic_token=cluster.token,
                                              include_awards=True,accredited_offer_sha=offer_sha,provenance=provenance)
                self.assertEqual(empty['inserted'],3);self.assertEqual(empty['awards_inserted'],2)
                self.assertEqual(dst.execute('SELECT notification_ingest_intent FROM document_versions WHERE sha256=%s',(sha,)).fetchone()['notification_ingest_intent'],'unknown')
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_unawarded.document_evidence').fetchone()['n'],1)
                dst.commit()
                src.execute('UPDATE award_assignments SET matched_offered_position_id=NULL WHERE award_result_id=41');src.commit()
                check=run()
                self.assertEqual(check['associations_quarantined'],1)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments WHERE matched_offered_position_id=300').fetchone()['n'],2)


if __name__=='__main__':unittest.main()

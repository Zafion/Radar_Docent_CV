"""Synthetic isolated PostgreSQL tests for secondary multiset reconciliation."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from app.services.historical_secondary_reconcile import reconcile_secondary, AWARD_FIELDS
from app.services.historical_19nov_reconcile import reconcile_19nov
from tests.postgres_isolated_validation import FreshCluster
from tests.test_historical_maestros_awards_postgres import SCHEMA


class SecondaryAdapterTests(unittest.TestCase):
    def test_partial_empty_complete_conflict_and_rollback(self):
        with FreshCluster() as cluster,tempfile.TemporaryDirectory() as temp:
            pdf=Path(temp)/'fixture.pdf';pdf.write_bytes(b'synthetic');sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src,psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db,version_id,document_id in ((src,10,20),(dst,100,200)):
                    db.execute(SCHEMA);db.execute('ALTER TABLE award_results ADD COLUMN body_name text');db.execute('ALTER TABLE documents ADD COLUMN doc_family text')
                    db.execute("INSERT INTO sources(source_key,source_url) VALUES('rrhh_main','https://ceice.gva.es/es/web/rrhh-educacion')")
                    for table in ('public_alerts','notification_posts','push_notification_events'):db.execute('INSERT INTO '+table+' VALUES(1)')
                    db.execute('INSERT INTO document_versions(id,sha256) VALUES(%s,%s)',(version_id,sha))
                    db.execute("INSERT INTO documents(id,document_version_id,doc_family) VALUES(%s,%s,'ignored')",(document_id,version_id));db.commit()
                src.execute("UPDATE documents SET doc_family='final_award_listing' WHERE id=20")
                src.execute("UPDATE document_versions SET notification_ingest_intent='live_new' WHERE id=10")
                src.execute("INSERT INTO document_parse_runs(document_version_id,status,rows_extracted) VALUES(10,'success',5)")
                src.execute("INSERT INTO document_versions(id,sha256) VALUES(11,%s)",('f'*64,))
                src.execute('INSERT INTO documents(id,document_version_id) VALUES(21,11)')
                src.execute("INSERT INTO offered_positions(id,document_id,position_code,center_code,specialty_code,body_code) VALUES(30,21,'P','C','001','A')")
                assignment=dict(zip(AWARD_FIELDS,('type','town','C','center','001','specialty','P','h',1.0,'request',1,'type','assignment')))
                spec=[(41,1,'Adjudicat','awarded','a'),(42,1,'No adjudicat','no-award','b'),
                      (43,2,'No adjudicat','duplicate','c'),(44,2,'No adjudicat','duplicate','c'),
                      (45,3,'No adjudicat','missing','d')]
                def add(db,row,document_id):
                    ident,ordinal,status,raw,name=row
                    db.execute('''INSERT INTO award_results(id,document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope,body_name)
                        VALUES(%s,%s,%s,%s,%s,%s,%s,'001','secundaria_otros','body')''',(ident,document_id,ordinal,status,raw,name,name.upper()))
                for r in spec:add(src,r,20)
                src.execute('''INSERT INTO award_assignments(award_result_id,assignment_kind,locality,center_code,center_name,position_specialty_code,position_specialty_name,position_code,hours_text,hours_value,petition_text,petition_number,request_type,raw_assignment_text,matched_offered_position_id)
                    VALUES(41,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,30)''',tuple(assignment.values()));src.commit()
                for r in (spec[0],spec[1],spec[2]):add(dst,(r[0]+400,*r[1:]),200)
                dst.execute('''INSERT INTO award_assignments(award_result_id,assignment_kind,locality,center_code,center_name,position_specialty_code,position_specialty_name,position_code,hours_text,hours_value,petition_text,petition_number,request_type,raw_assignment_text)
                    VALUES(441,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',tuple(assignment.values()));dst.commit()
                parsed=[SimpleNamespace(order_number=i,status=status,raw_block_text=raw,person_display_name=name,person_name_normalized=name.upper(),specialty_code='001',body_name='body',assignment=SimpleNamespace(**assignment) if ident==41 else None) for ident,i,status,raw,name in spec]
                def run():
                    with patch('app.services.historical_secondary_reconcile.FinalAwardListingSecundariaParserService') as parser:
                        parser.return_value._parse_document.return_value=parsed
                        return reconcile_secondary(src,dst,document_sha=sha,pdf_path=pdf,synthetic_token=cluster.token)
                dst.execute("CREATE FUNCTION fail_secondary() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
                dst.execute('CREATE TRIGGER fail_secondary BEFORE INSERT ON award_results FOR EACH ROW EXECUTE FUNCTION fail_secondary()');dst.commit()
                with self.assertRaises(psycopg.Error):run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],3)
                self.assertIsNone(dst.execute("SELECT to_regnamespace('recovery_secondary') AS n").fetchone()['n']);dst.commit()
                dst.execute('DROP TRIGGER fail_secondary ON award_results');dst.commit()
                first=run();self.assertEqual(first['inserted'],2);self.assertEqual(first['awards_reused'],1);self.assertEqual(first['links_pending'],1)
                # The 19-Nov entrypoint must isolate a failed Maestros document
                # and still execute the real Secondary adapter transaction.
                missing={'document_sha':'0'*64,'pdf_path':Path(temp)/'missing.pdf'}
                real={'document_sha':sha,'pdf_path':pdf}
                with patch('app.services.historical_secondary_reconcile.FinalAwardListingSecundariaParserService') as parser:
                    parser.return_value._parse_document.return_value=parsed
                    combined=reconcile_19nov(src,dst,maestros=missing,secondary=real,execute=True,synthetic_token=cluster.token)
                self.assertEqual(combined['documents']['maestros']['status'],'failed')
                self.assertEqual(combined['documents']['secondary']['status'],'applied')
                self.assertEqual(combined['documents']['secondary']['result'].get('inserted',0),0)
                self.assertEqual(combined['offer_matches_created'],0)
                again=run();self.assertEqual(again.get('inserted',0),0)
                self.assertEqual(dst.execute('SELECT doc_family FROM documents WHERE id=200').fetchone()['doc_family'],'ignored')
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],5)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results WHERE raw_block_text=%s',('duplicate',)).fetchone()['n'],2)
                for table in ('public_alerts','notification_posts','push_notification_events'):self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],1)
                dst.commit()
                dst.execute('DELETE FROM award_assignments');dst.execute('DELETE FROM award_results');dst.commit()
                dst.execute("CREATE FUNCTION fail_secondary_award() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected award'; END $$")
                dst.execute('CREATE TRIGGER fail_secondary_award BEFORE INSERT ON award_assignments FOR EACH ROW EXECUTE FUNCTION fail_secondary_award()');dst.commit()
                with self.assertRaises(psycopg.Error):run()
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],0);dst.commit()
                dst.execute('DROP TRIGGER fail_secondary_award ON award_assignments');dst.commit()
                empty=run();self.assertEqual(empty['inserted'],5);self.assertEqual(empty['awards_inserted'],1)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments WHERE matched_offered_position_id IS NOT NULL').fetchone()['n'],0);dst.commit()
                dst.execute("UPDATE award_results SET raw_block_text='conflicting' WHERE order_number=3");dst.commit()
                conflict=run();self.assertEqual(conflict.get('inserted',0),0);self.assertGreater(conflict['quarantined'],0)
                dst.commit();dst.execute('DELETE FROM award_assignments');dst.execute('DELETE FROM award_results')
                dst.execute('DELETE FROM recovery_secondary.quarantine');dst.execute('DELETE FROM documents WHERE id=200')
                dst.execute('DELETE FROM document_versions WHERE id=100');dst.commit()
                url='https://ceice.gva.es/documents/fixture.pdf';html=('<a href="'+url+'">fixture</a>').encode()
                proof={'html_bytes':html,'html_sha256':hashlib.sha256(html).hexdigest(),'page_url':'https://ceice.gva.es/es/fixture','document_url':url}
                with patch('app.services.historical_secondary_reconcile.FinalAwardListingSecundariaParserService') as parser:
                    parser.return_value._parse_document.return_value=parsed
                    result=reconcile_secondary(src,dst,document_sha=sha,pdf_path=pdf,synthetic_token=cluster.token,provenance=proof)
                self.assertEqual(result['inserted'],5);self.assertEqual(result['awards_inserted'],1)
                self.assertEqual(dst.execute('SELECT notification_ingest_intent FROM document_versions WHERE sha256=%s',(sha,)).fetchone()['notification_ingest_intent'],'unknown')


if __name__=='__main__':unittest.main()

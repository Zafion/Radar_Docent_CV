"""Combined entrypoint using an isolated PostgreSQL and synthetic documents."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from app.services.historical_19nov_reconcile import reconcile_19nov, quarantine_offers
from app.services.offered_positions_parser import ParsedOfferedPosition
from tests.postgres_isolated_validation import FreshCluster
from tests.test_historical_maestros_awards_postgres import SCHEMA


class Combined19Tests(unittest.TestCase):
    def test_offer_multiset_quarantine_without_promotion(self):
        with FreshCluster() as cluster, tempfile.TemporaryDirectory() as directory:
            paths=[Path(directory)/'p.pdf',Path(directory)/'d.pdf']
            for p in paths:p.write_bytes(p.name.encode())
            shas=[hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src, psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db,offset in ((src,0),(dst,100)):
                    db.execute(SCHEMA)
                    for field in ('source_type','body_name','specialty_name','province','locality','center_name','hours_text','valenciano_required_text','position_type','composition','observations','raw_row_text'):
                        db.execute('ALTER TABLE offered_positions ADD COLUMN '+field+' text')
                    db.execute('ALTER TABLE offered_positions ADD COLUMN hours_value double precision')
                    db.execute('ALTER TABLE offered_positions ADD COLUMN is_itinerant boolean')
                    for i,sha in enumerate(shas,1):
                        db.execute('INSERT INTO document_versions(id,sha256) VALUES(%s,%s)',(i+offset,sha))
                        db.execute('INSERT INTO documents(id,document_version_id) VALUES(%s,%s)',(i+offset,i+offset))
                    db.commit()
                row=ParsedOfferedPosition('fixture',None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,'fixture')
                for doc in (1,2):
                    src.execute("INSERT INTO offered_positions(document_id,source_type,raw_row_text) SELECT %s,'fixture','fixture' FROM generate_series(1,299)",(doc,))
                src.commit()
                offers=[]
                for i in range(2):
                    url='https://ceice.gva.es/documents/'+paths[i].name
                    html=('<a href="'+url+'">fixture</a>').encode()
                    proof={'html_bytes':html,'html_sha256':hashlib.sha256(html).hexdigest(),'page_url':'https://ceice.gva.es/es/fixture','document_url':url}
                    offers.append(dict(document_sha=shas[i],pdf_path=paths[i],provenance=proof,stage_label=('provisional','definitivo')[i]))
                    with patch('app.services.historical_19nov_reconcile.OfferedPositionsParserService') as parser:
                        parser.return_value._parse_document.return_value=[row]*299
                        args=dict(document_sha=shas[i],pdf_path=paths[i],provenance=proof,stage_label=('provisional','definitivo')[i],synthetic_token=cluster.token)
                        if i == 1:
                            dst.execute("CREATE FUNCTION fail_offer() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'test'; END $$")
                            dst.execute('CREATE TRIGGER fail_offer BEFORE INSERT ON recovery_quarantine.positions_19nov FOR EACH ROW EXECUTE FUNCTION fail_offer()');dst.commit()
                            with self.assertRaises(psycopg.Error): quarantine_offers(src,dst,**args)
                            self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n'],299)
                            dst.execute('DROP TRIGGER fail_offer ON recovery_quarantine.positions_19nov');dst.commit()
                        self.assertEqual(quarantine_offers(src,dst,**args)['inserted'],299)
                        self.assertEqual(quarantine_offers(src,dst,**args)['inserted'],0)
                with patch('app.services.historical_19nov_reconcile.OfferedPositionsParserService') as parser:
                    parser.return_value._parse_document.return_value=[row]*299
                    missing1={'document_sha':'a'*64,'pdf_path':Path(directory)/'missing1'}
                    missing2={'document_sha':'b'*64,'pdf_path':Path(directory)/'missing2'}
                    report=reconcile_19nov(src,dst,maestros=missing1,secondary=missing2,offers=offers,execute=True,synthetic_token=cluster.token)
                    self.assertEqual([item['result']['inserted'] for item in report['offers']],[0,0])
                    self.assertEqual(report['status'],'partial')
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n'],598)
                self.assertEqual(dst.execute('SELECT count(*) AS n FROM offered_positions').fetchone()['n'],0)

    def test_both_documents_idempotence_and_independent_rollback(self):
        with FreshCluster() as cluster, tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory)/'m.pdf', Path(directory)/'s.pdf']
            for p in paths: p.write_bytes(p.name.encode())
            shas = [hashlib.sha256(p.read_bytes()).hexdigest() for p in paths]
            with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture')
                admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src, psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db in (src,dst):
                    db.execute(SCHEMA)
                    db.execute('ALTER TABLE award_results ADD COLUMN body_name text')
                    db.execute('ALTER TABLE documents ADD COLUMN doc_family text')
                    db.execute('''CREATE TABLE historical_procedures(procedure_key text PRIMARY KEY,procedure_kind text,
                      procedure_date date,date_evidence text,publication_date date,publication_date_quality text);
                      CREATE TABLE historical_procedure_documents(procedure_key text REFERENCES historical_procedures,
                      document_id bigint REFERENCES documents,document_role text,relationship_evidence text,PRIMARY KEY(procedure_key,document_id));''')
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        db.execute('INSERT INTO '+table+' VALUES(1)')
                    db.commit()
                for db, offset in ((src,0),(dst,100)):
                    for i, sha in enumerate(shas,1):
                        db.execute('INSERT INTO document_versions(id,sha256) VALUES(%s,%s)',(offset+i,sha))
                        db.execute("INSERT INTO documents(id,document_version_id,doc_family) VALUES(%s,%s,'ignored')",(offset+i,offset+i))
                    db.commit()
                src.execute("INSERT INTO historical_procedures VALUES('interino-2026-11-19','interino','2026-11-19','source',NULL,'unknown')")
                for i in (1,2):
                    src.execute("INSERT INTO historical_procedure_documents VALUES('interino-2026-11-19',%s,'award','explicit source')",(i,))
                parsed=[]
                for i, scope in ((1,'maestros'),(2,'secundaria_otros')):
                    src.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope,body_name) VALUES(%s,1,'No adjudicat',%s,'fixture','FIXTURE','001',%s,'body')",(i,'block'+str(i),scope))
                    parsed.append([SimpleNamespace(order_number=1,status='No adjudicat',raw_block_text='block'+str(i),person_display_name='fixture',person_name_normalized='FIXTURE',specialty_code='001',body_name='body',assignment=None)])
                src.commit()
                args=dict(maestros={'document_sha':shas[0],'pdf_path':paths[0]},secondary={'document_sha':shas[1],'pdf_path':paths[1]},execute=True,synthetic_token=cluster.token)
                with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as m, patch('app.services.historical_secondary_reconcile.FinalAwardListingSecundariaParserService') as s:
                    m.return_value._parse_document.return_value=parsed[0];s.return_value._parse_document.return_value=parsed[1]
                    dst.execute("CREATE FUNCTION fail_secondary_row() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.list_scope='secundaria_otros' THEN RAISE EXCEPTION 'test'; END IF; RETURN NEW; END $$")
                    dst.execute('CREATE TRIGGER fail_secondary BEFORE INSERT ON award_results FOR EACH ROW EXECUTE FUNCTION fail_secondary_row()');dst.commit()
                    first=reconcile_19nov(src,dst,**args)
                    self.assertEqual(first['documents']['maestros']['status'],'applied')
                    self.assertEqual(first['documents']['secondary']['status'],'failed')
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],1)
                    dst.execute('DROP TRIGGER fail_secondary ON award_results');dst.commit()
                    second=reconcile_19nov(src,dst,**args)
                    self.assertEqual(second['documents']['secondary']['status'],'applied')
                    again=reconcile_19nov(src,dst,**args)
                    self.assertEqual(again['documents']['secondary']['result'].get('inserted',0),0)
                    self.assertEqual(again['documents']['maestros']['result'].get('inserted',0),0)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_procedure_documents').fetchone()['n'],2)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],2)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM offered_positions').fetchone()['n'],0)
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],1)
                    self.assertEqual(dst.execute("SELECT count(*) AS n FROM documents WHERE doc_family='ignored'").fetchone()['n'],2)

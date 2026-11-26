"""Combined PostgreSQL test using real adapters and synthetic parsed PDFs."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster
from tests.test_historical_maestros_awards_postgres import SCHEMA, FIELDS
from app.services.historical_cumulative_reconcile import reconcile_cumulative
from app.services.unawarded_expert_vacancies_parser import UnawardedExpertVacancy
from app.services.offered_positions_parser import ParsedOfferedPosition


class RealAdaptersTest(unittest.TestCase):
    def test_six_documents_and_quarantine(self):
        with FreshCluster() as cluster,tempfile.TemporaryDirectory() as temp:
            keys=('700','436','701','expert','1000067','1000444','p','d')
            specs={}
            for k in keys:
                path=Path(temp)/k;path.write_bytes(b'%PDF-'+k.encode());url='https://ceice.gva.es/documents/'+k
                html=('<a href="'+url+'">fixture</a>').encode()
                specs[k]={'pdf_path':path,'document_sha':hashlib.sha256(path.read_bytes()).hexdigest(),
                          'provenance':{'html_bytes':html,'html_sha256':hashlib.sha256(html).hexdigest(),
                                        'page_url':'https://ceice.gva.es/es/fixture','document_url':url}}
            specs['expert']['procedure_key']='expertos-2026-11-05'
            for k,label in (('p','provisional'),('d','definitivo')):specs[k]['stage_label']=label
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn('source_fixture'),row_factory=dict_row) as src,psycopg.connect(cluster.dsn(),row_factory=dict_row) as dst:
                for db,offset in ((src,0),(dst,100)):
                    db.execute(SCHEMA);db.execute('ALTER TABLE documents ADD COLUMN doc_family text');db.execute('ALTER TABLE award_results ADD COLUMN body_name text')
                    for field in ('source_type','body_name','specialty_name','province','locality','center_name','hours_text','valenciano_required_text','position_type','composition','observations','raw_row_text'):
                        db.execute('ALTER TABLE offered_positions ADD COLUMN '+field+' text')
                    db.execute('ALTER TABLE offered_positions ADD COLUMN hours_value double precision');db.execute('ALTER TABLE offered_positions ADD COLUMN is_itinerant boolean')
                    db.execute('''CREATE TABLE historical_procedures(procedure_key text PRIMARY KEY,procedure_kind text,procedure_date date,date_evidence text,publication_date date,publication_date_quality text);
                      CREATE TABLE historical_procedure_documents(procedure_key text REFERENCES historical_procedures,document_id bigint REFERENCES documents,document_role text,relationship_evidence text,PRIMARY KEY(procedure_key,document_id));
                      CREATE TABLE historical_unawarded_expert_vacancies(document_id bigint REFERENCES documents,center_code text,source_position_id text,province text,locality text,center_name text,cycle text,modules text,teaching_hours text,total_hours text,valencian_source text,result_status text,PRIMARY KEY(document_id,center_code,source_position_id));''')
                    db.execute("INSERT INTO sources(id,source_key,source_url) VALUES(1,'rrhh_main','https://ceice.gva.es/es/web/rrhh-educacion'),(2,'rrhh_novedades','https://ceice.gva.es/es/novedades')")
                    for table in ('public_alerts','notification_posts','push_notification_events'):db.execute('INSERT INTO '+table+' VALUES(1)')
                    for i,k in enumerate(keys,1):
                        db.execute('INSERT INTO document_versions(id,sha256) VALUES(%s,%s)',(offset+i,specs[k]['document_sha']))
                        db.execute("INSERT INTO documents(id,document_version_id,source_id,doc_family) VALUES(%s,%s,2,'ignored')",(offset+i,offset+i))
                    db.commit()
                for key,date in (('interino-2026-11-03','2026-11-03'),('expertos-2026-11-05','2026-11-05'),('interino-2026-11-19','2026-11-19')):
                    src.execute("INSERT INTO historical_procedures VALUES(%s,'fixture',%s,'document',NULL,'unknown')",(key,date))
                for proc,ids in (('interino-2026-11-03',(1,3)),('expertos-2026-11-05',(4,)),('interino-2026-11-19',(5,6,7,8))):
                    for ident in ids:src.execute("INSERT INTO historical_procedure_documents VALUES(%s,%s,'fixture','explicit')",(proc,ident))
                parsed={}
                for i,k in enumerate(keys[:6],1):
                    if k=='expert':continue
                    scope='secundaria_otros' if k in ('701','1000444') else 'maestros';raw='block-'+k
                    status='Adjudicat' if k=='700' else 'No adjudicat'
                    rid=src.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope,body_name) VALUES(%s,1,%s,%s,'fixture','FIXTURE','001',%s,'body') RETURNING id",(i,status,raw,scope)).fetchone()['id']
                    award=dict(zip(FIELDS,('type','town','C','center','001','specialty','P','h',1.0,'req',1,'type','assignment'))) if k=='700' else None
                    if award:src.execute('''INSERT INTO award_assignments(award_result_id,assignment_kind,locality,center_code,center_name,position_specialty_code,position_specialty_name,position_code,hours_text,hours_value,petition_text,petition_number,request_type,raw_assignment_text) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(rid,*award.values()))
                    parsed[k]=[SimpleNamespace(order_number=1,status=status,raw_block_text=raw,person_display_name='fixture',person_name_normalized='FIXTURE',specialty_code='001',body_name='body',assignment=SimpleNamespace(**award) if award else None)]
                    if k=='1000444':
                        src.execute("INSERT INTO award_results(document_id,order_number,status,raw_block_text,person_display_name,person_name_normalized,specialty_code,list_scope,body_name) VALUES(%s,1,%s,%s,'fixture','FIXTURE','001',%s,'body')",(i,status,raw,scope))
                        parsed[k].append(parsed[k][0])
                src.execute("INSERT INTO document_parse_runs(document_version_id,status,rows_extracted) VALUES(4,'success',6)")
                expert=[]
                for i in range(6):
                    v=UnawardedExpertVacancy('c'+str(i),'p'+str(i),'p','l','c','cy','m','h','h','n');expert.append(v)
                    src.execute("INSERT INTO historical_unawarded_expert_vacancies VALUES(4,%s,%s,'p','l','c','cy','m','h','h','n','unawarded_no_requests')",(v.center_code,v.source_position_id))
                for i in (7,8):src.execute("INSERT INTO offered_positions(document_id,source_type,raw_row_text) SELECT %s,'fixture','fixture' FROM generate_series(1,299)",(i,))
                src.commit()
                offer=ParsedOfferedPosition('fixture',None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,None,'fixture')
                docs={k:specs[k] for k in keys[:6]};offers=[specs['p'],specs['d']]
                with patch('app.services.historical_unawarded_reconcile.FinalAwardListingMaestrosParserService') as ma, patch('app.services.historical_secondary_reconcile.FinalAwardListingSecundariaParserService') as se, patch('app.services.historical_19nov_reconcile.OfferedPositionsParserService') as po, patch('app.services.historical_expert_reconcile.parse_expert_pdf',return_value=('2026-11-05',expert)):
                    ma.return_value._parse_document.side_effect=lambda d:parsed[Path(d['file_path']).name]
                    se.return_value._parse_document.side_effect=lambda d:parsed[Path(d['file_path']).name]
                    po.return_value._parse_document.return_value=[offer]*299
                    dst.execute("CREATE FUNCTION fail_award() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected'; END $$")
                    dst.execute('CREATE TRIGGER fail_award BEFORE INSERT ON award_assignments FOR EACH ROW EXECUTE FUNCTION fail_award()');dst.commit()
                    first=reconcile_cumulative(src,dst,documents=docs,offers=offers,execute=True,synthetic_token=cluster.token)
                    self.assertEqual(first['documents']['700']['status'],'failed',first)
                    self.assertEqual(first['documents']['701']['status'],'applied',first)
                    self.assertEqual([x['result']['inserted'] for x in first['quarantine']],[299,299])
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results WHERE document_id=101').fetchone()['n'],0)
                    dst.execute('DROP TRIGGER fail_award ON award_assignments');dst.commit()
                    second=reconcile_cumulative(src,dst,documents=docs,offers=offers,execute=True,synthetic_token=cluster.token)
                    self.assertEqual([second['documents'][k]['status'] for k in keys[:6]],['applied']*6,second)
                    self.assertEqual([x['result']['inserted'] for x in second['quarantine']],[0,0])
                    third=reconcile_cumulative(src,dst,documents=docs,offers=offers,execute=True,synthetic_token=cluster.token)
                    self.assertEqual(third['documents']['700']['result'].get('inserted',0),0)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_results').fetchone()['n'],6)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM award_assignments').fetchone()['n'],1)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n'],598)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM offered_positions').fetchone()['n'],0)
                    self.assertEqual(dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies').fetchone()['n'],6)
                    for table in ('public_alerts','notification_posts','push_notification_events'):self.assertEqual(dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n'],1)

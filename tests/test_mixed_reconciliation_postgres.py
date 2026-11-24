"""Synthetic PostgreSQL test for mixed-row planning, idempotence and rollback."""
import unittest
import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster
from app.services.historical_batch_reconcile import plan_mixed_rows,apply_mixed_plan
from app.services.historical_batch_reconcile import create_verified_document,copy_bag,load_mixed_rows,apply_mixed_difficult,apply_mixed_bag

class MixedPostgresTests(unittest.TestCase):
    def test_mixed_transaction_and_duplicate_document_keys(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as db:db.execute('CREATE DATABASE funk_validation')
            with psycopg.connect(cluster.dsn(),row_factory=dict_row) as db:
                db.execute('CREATE TABLE rows (source_id int PRIMARY KEY, doc text NOT NULL, ordinal int, flag boolean)')
                db.execute("INSERT INTO rows VALUES(1,'sha',1,NULL),(2,'sha',2,true)")
                source=[{'source_id':11,'doc':'sha','ordinal':1,'flag':False},
                        {'source_id':12,'doc':'sha','ordinal':2,'flag':True},
                        {'source_id':13,'doc':'sha','ordinal':3,'flag':True},
                        {'source_id':14,'doc':'sha','ordinal':4,'flag':True},
                        {'source_id':15,'doc':'sha','ordinal':4,'flag':True}]
                def apply():
                    old=db.execute('SELECT * FROM rows ORDER BY source_id').fetchall()
                    plan=plan_mixed_rows(old,source,['doc','ordinal'],['flag']);quarantine=[]
                    def insert(source_id,reason):
                        row=next(x for x in source if x['source_id']==source_id)
                        db.execute('INSERT INTO rows VALUES(%s,%s,%s,%s)',tuple(row[x] for x in ('source_id','doc','ordinal','flag')))
                    result=apply_mixed_plan(plan,insert,lambda *x:quarantine.append(x));return result,quarantine
                first,q=apply();self.assertEqual(first,{'C':1,'A':1,'B':1,'E':2});self.assertEqual(len(q),3);db.commit()
                second,_=apply();self.assertNotIn('B',second);self.assertEqual(db.execute('SELECT count(*) AS n FROM rows').fetchone()['n'],3);db.commit()
                try:
                    with db.transaction():
                        db.execute("INSERT INTO rows VALUES(99,'sha',99,true)")
                        raise RuntimeError('injected')
                except RuntimeError:pass
                self.assertEqual(db.execute('SELECT count(*) AS n FROM rows').fetchone()['n'],3)

    def test_new_document_metadata_and_rows_in_two_synthetic_databases(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE destination_fixture')
            schema='''CREATE TABLE sources(id bigserial PRIMARY KEY,source_key text UNIQUE,source_url text);
              CREATE TABLE document_versions(id bigserial PRIMARY KEY,sha256 text UNIQUE NOT NULL);
              CREATE TABLE document_parse_runs(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id),parser_key text);
              CREATE TABLE documents(id bigserial PRIMARY KEY,document_version_id bigint REFERENCES document_versions(id),source_id bigint REFERENCES sources(id),
                 doc_family text,validation_status text,validation_parse_run_id bigint REFERENCES document_parse_runs(id),historical_status text,publication_date_quality text);
              CREATE TABLE docent_bag_snapshots(id bigserial PRIMARY KEY,document_id bigint REFERENCES documents(id),staff_kind text,list_scope text,position_scope text,specialty_code text,document_url text,source_page_url text,body_code text,course_year text,list_stage text);
              CREATE TABLE docent_bag_members(id bigserial PRIMARY KEY,snapshot_id bigint REFERENCES docent_bag_snapshots(id),order_number int,person_display_name text,masked_dni text,person_name_normalized text,service_status text,collective text);
              CREATE TABLE difficult_coverage_positions(id bigserial PRIMARY KEY,document_id bigint REFERENCES documents(id),body_code text,specialty_code text,center_code text,position_code text,center_name text,locality text,num_participants int,sorteo_number text,registro_superior text,registro_inferior text);
              CREATE TABLE difficult_coverage_candidates(id bigserial PRIMARY KEY,position_id bigint REFERENCES difficult_coverage_positions(id),row_number int,full_name_normalized text,is_selected bool,registration_code_or_bag_order text,petition_number int,assigned_position_code text);
              CREATE SCHEMA recovery_batch;
              CREATE TABLE recovery_batch.row_map(kind text,source_id bigint,target_id bigint,PRIMARY KEY(kind,source_id));
              CREATE TABLE recovery_batch.quarantined_rows(kind text,source_id bigint,source_sha text,reason text,PRIMARY KEY(kind,source_id));
              CREATE TABLE recovery_batch.mixed_changes(source_sha text,kind text,source_id bigint,target_id bigint,justification text,PRIMARY KEY(kind,source_id));'''
            source_dsn=cluster.dsn('postgres').replace('dbname=postgres','dbname=source_fixture')
            destination_dsn=cluster.dsn('postgres').replace('dbname=postgres','dbname=destination_fixture')
            with psycopg.connect(source_dsn,row_factory=dict_row) as source,psycopg.connect(destination_dsn,row_factory=dict_row) as dest:
                source.execute(schema);dest.execute(schema)
                official='https://ceice.gva.es/es/web/rrhh-educacion'
                source.execute("INSERT INTO sources(id,source_key,source_url) VALUES(17,'fixture','https://example.invalid')")
                dest.execute('INSERT INTO sources(id,source_key,source_url) VALUES(44,%s,%s)',('rrhh_main',official))
                source.execute("INSERT INTO document_versions(id,sha256) VALUES(20,%s)",('f'*64,))
                source.execute("INSERT INTO document_parse_runs(id,document_version_id,parser_key) VALUES(30,20,'synthetic')")
                source.execute("INSERT INTO documents(id,document_version_id,source_id,doc_family) VALUES(10,20,17,'career_practice')")
                source.execute("INSERT INTO docent_bag_snapshots(id,document_id,staff_kind,list_scope,position_scope,specialty_code) VALUES(90,10,'career_practice','maestros','specialty','001')")
                source.execute("INSERT INTO docent_bag_members(id,snapshot_id,order_number,person_display_name,masked_dni,person_name_normalized) VALUES(100,90,1,'Fixture','***1','FIXTURE'),(101,90,2,'Unique','***2','UNIQUE')")
                dest.execute("INSERT INTO document_versions(id,sha256) VALUES(71,%s)",('e'*64,))
                dest.execute("INSERT INTO documents(id,document_version_id,source_id,doc_family) VALUES(72,71,44,'career_practice')")
                dest.execute("INSERT INTO docent_bag_snapshots(id,document_id,staff_kind,list_scope,position_scope,specialty_code) VALUES(73,72,'career_practice','maestros','specialty','001')")
                dest.execute("INSERT INTO docent_bag_members(id,snapshot_id,order_number,person_display_name,masked_dni,person_name_normalized) VALUES(74,73,1,'Different fixture','***1','DIFFERENT')")
                source.commit();dest.commit()
                parse=source.execute('SELECT * FROM document_parse_runs WHERE id=30').fetchone()
                with dest.transaction():
                    did=create_verified_document(source,dest,{'id':10,'document_version_id':20},parse,{'source_key':'rrhh_main','source_url':official})
                    added=copy_bag(source,dest,10,did,'https://ceice.gva.es/documents/synthetic.pdf','f'*64)
                self.assertEqual(added['bag_members'],1)
                self.assertEqual(added['ambiguous_rows'],1)
                self.assertEqual(added['cross_document_review']['source_rows_with_possible_overlap'],1)
                self.assertEqual(added['cross_document_review']['merged'],0)
                row=dest.execute('''SELECT d.source_id,d.validation_status,d.historical_status,d.publication_date_quality,s.staff_kind,m.order_number
                    FROM documents d JOIN docent_bag_snapshots s ON s.document_id=d.id JOIN docent_bag_members m ON m.snapshot_id=s.id WHERE d.id=%s''',(did,)).fetchone()
                self.assertEqual((row['source_id'],row['validation_status'],row['historical_status'],row['publication_date_quality'],row['staff_kind'],row['order_number']),
                                 (44,'needs_validation','unknown','unknown','career_practice',2))
                source.execute("INSERT INTO docent_bag_members(id,snapshot_id,order_number,person_display_name,masked_dni,person_name_normalized) VALUES(102,90,3,'Third','***3','THIRD')")
                source.commit();dest.commit()
                old_bag,bag_keys,bag_fields=load_mixed_rows(dest,did,'bag_members');new_bag,_,_=load_mixed_rows(source,10,'bag_members')
                bag_plan=plan_mixed_rows(old_bag,new_bag,bag_keys,bag_fields)
                with dest.transaction():bag_result=apply_mixed_bag(source,dest,bag_plan,did,10,'f'*64)
                self.assertEqual(bag_result['inserted'],1);self.assertEqual(bag_result['new_rows_quarantined'],1)
                old_bag,_,_=load_mixed_rows(dest,did,'bag_members')
                with dest.transaction():bag_again=apply_mixed_bag(source,dest,plan_mixed_rows(old_bag,new_bag,bag_keys,bag_fields),did,10,'f'*64)
                self.assertNotIn('inserted',bag_again)
                source.execute("INSERT INTO difficult_coverage_positions(id,document_id,body_code,specialty_code,center_code,position_code,center_name,locality,num_participants) VALUES(200,10,'A','S','C','P','Center','Town',2)")
                dest.execute("INSERT INTO difficult_coverage_positions(id,document_id,body_code,specialty_code,center_code,position_code,center_name,locality,num_participants) VALUES(201,%s,'A','S','C','P','Center','Town',2)",(did,))
                source.execute("INSERT INTO difficult_coverage_candidates(id,position_id,row_number,full_name_normalized,is_selected) VALUES(300,200,1,'FIXTURE',false)")
                source.commit();dest.commit()
                old,keys,fields=load_mixed_rows(dest,did,'difficult_candidates');new,_,_=load_mixed_rows(source,10,'difficult_candidates')
                plan=plan_mixed_rows(old,new,keys,fields)
                with dest.transaction():result=apply_mixed_difficult(source,dest,plan,did,'f'*64)
                self.assertEqual(result['inserted'],1)
                old,_,_=load_mixed_rows(dest,did,'difficult_candidates')
                plan=plan_mixed_rows(old,new,keys,fields)
                with dest.transaction():again=apply_mixed_difficult(source,dest,plan,did,'f'*64)
                self.assertNotIn('inserted',again);self.assertEqual(again['A'],1)

if __name__=='__main__':unittest.main()

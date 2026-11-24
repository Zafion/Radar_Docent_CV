"""End-to-end D-path on synthetic PostgreSQL databases and the real ASGI API."""
import asyncio,os,unittest
from unittest.mock import patch
import httpx,psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster,ROOT
from app.services.historical_batch_reconcile import create_verified_document,copy_bag

class NewDocumentPostgresApiTests(unittest.TestCase):
    def test_new_sha_metadata_rows_and_profile(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                admin.execute('CREATE DATABASE source_fixture');admin.execute('CREATE DATABASE destination_fixture')
            def dsn(db):return make_conninfo(cluster.dsn('postgres'),dbname=db)
            schema=(ROOT/'app/storage/schema.sql').read_text()
            with psycopg.connect(dsn('source_fixture'),row_factory=dict_row) as source, psycopg.connect(dsn('destination_fixture'),row_factory=dict_row) as dest:
                source.execute(schema);dest.execute(schema)
                dest.execute('CREATE SCHEMA recovery_batch; CREATE TABLE recovery_batch.row_map(kind text,source_id bigint,target_id bigint,PRIMARY KEY(kind,source_id)); CREATE TABLE recovery_batch.quarantined_rows(kind text,source_id bigint,source_sha text,reason text,PRIMARY KEY(kind,source_id))')
                source.execute("INSERT INTO sources(source_key,source_url,label) VALUES('fixture','https://example.invalid','fixture')")
                dest.execute("INSERT INTO sources(source_key,source_url,label) VALUES('rrhh_main','https://ceice.gva.es/es/web/rrhh-educacion','fixture')")
                sid=source.execute("SELECT id FROM sources WHERE source_key='fixture'").fetchone()['id']
                vid=source.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at) VALUES(%s,'synthetic.pdf','synthetic.pdf','/synthetic',1,now()) RETURNING id",('f'*64,)).fetchone()['id']
                did=source.execute("INSERT INTO documents(document_version_id,source_id,doc_family) VALUES(%s,%s,'docent_career_practice_participants') RETURNING id",(vid,sid)).fetchone()['id']
                run=source.execute("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at,finished_at,rows_extracted) VALUES(%s,'fixture','1','success',now(),now(),1) RETURNING *",(vid,)).fetchone()
                snap=source.execute("INSERT INTO docent_bag_snapshots(document_id,list_stage,staff_kind,position_scope,list_scope,specialty_code) VALUES(%s,'definitive','career_practice','specialty','maestros','001') RETURNING id",(did,)).fetchone()['id']
                source.execute("INSERT INTO docent_bag_members(snapshot_id,order_number,masked_dni,person_display_name,person_name_normalized,raw_row_text) VALUES(%s,1,'***1','SYNTHETIC PERSON','SYNTHETIC PERSON','fixture')",(snap,))
                source.commit();dest.commit()
                with dest.transaction():
                    target=create_verified_document(source,dest,{'id':did,'document_version_id':vid},run,{'source_key':'rrhh_main','source_url':'https://ceice.gva.es/es/web/rrhh-educacion'})
                    inserted=copy_bag(source,dest,did,target,'https://ceice.gva.es/documents/synthetic.pdf','f'*64)
                    self.assertEqual(inserted['bag_members'],1)
                counts={t:dest.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('public_alerts','notification_posts','push_notification_events')}
                self.assertEqual(counts,{'public_alerts':0,'notification_posts':0,'push_notification_events':0})
            env={'RADAR_DOCENT_DB_URL':make_conninfo(dsn('destination_fixture'),options='-c default_transaction_read_only=on'),'ENABLE_NOTIFICATIONS':'0'}
            with patch.dict(os.environ,env):
                from app.api.main import app
                from app.storage.db import close_pool
                async def check():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                        r=await client.get('/api/persons/profile',params={'normalized_name':'SYNTHETIC PERSON'})
                        self.assertEqual(r.status_code,200)
                        rows=r.json()['bag_memberships'];self.assertEqual(len(rows),1)
                        self.assertEqual((rows[0]['source_document_sha256'],rows[0]['staff_kind'],rows[0]['historical_status'],rows[0]['publication_date_quality']),
                                         ('f'*64,'career_practice','unknown','unknown'))
                try:asyncio.run(check())
                finally:close_pool()

if __name__=='__main__':unittest.main()

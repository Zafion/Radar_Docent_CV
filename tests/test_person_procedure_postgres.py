"""Exercise the real ASGI person endpoints against disposable PostgreSQL."""
import asyncio
from contextlib import ExitStack
import hashlib
import os
import unittest
from unittest.mock import patch

import httpx
import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster, ROOT, connection_guards


class PersonProcedurePostgresTests(unittest.TestCase):
    def test_endpoints_keep_procedures_separate(self):
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as c:
                cluster.verify(c);c.execute('CREATE DATABASE funk_validation')
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0')
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                list(connection_guards(cluster,stack))
                from app.storage.db import init_db,close_pool
                stack.callback(close_pool);init_db(str(ROOT/'app/storage/schema.sql'))
                from app.api.main import app
                records=[('SOLO INTERINO','interino',5,'2026-07-01',348),
                         ('SOLO CARRERA','career_practice',1,'2026-07-02',404),
                         ('MIXTA','interino',5,'2026-07-01',349),('MIXTA','career_practice',1,'2026-07-02',404),
                         ('MULTIPLE','interino',10,'2026-07-01',348),('MULTIPLE','interino',4,'2026-07-02',349),
                         ('REPETIDA','interino',8,'2026-07-01',348),('REPETIDA','interino',8,'2026-07-02',349),
                         ('AMBIGUA','unknown',1,'2026-07-01',404)]
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                    sid=c.execute("INSERT INTO sources(source_key,source_url,label) VALUES('fixture','https://example.invalid','fixture') RETURNING id").fetchone()['id']
                    for idx,(name,kind,order,day,version) in enumerate(records):
                        sha=hashlib.sha256(str(idx).encode()).hexdigest()
                        vid=c.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at) VALUES(%s,%s,%s,'/fixture',1,now()) RETURNING id",(sha,'version-'+str(version)+'.pdf','fixture-'+str(idx))).fetchone()['id']
                        family='docent_career_practice_participants' if kind=='career_practice' else 'docent_bag_participants'
                        did=c.execute("INSERT INTO documents(document_version_id,source_id,doc_family,list_scope,document_date_iso) VALUES(%s,%s,%s,'maestros',%s) RETURNING id",(vid,sid,family,day)).fetchone()['id']
                        snap=c.execute("INSERT INTO docent_bag_snapshots(document_id,course_year,list_stage,staff_kind,position_scope,list_scope,specialty_code) VALUES(%s,'2026/2027','provisional',%s,'specialty','maestros',%s) RETURNING id",(did,kind,str(idx))).fetchone()['id']
                        c.execute("INSERT INTO docent_bag_members(snapshot_id,order_number,person_display_name,person_name_normalized,collective,raw_row_text) VALUES(%s,%s,%s,%s,'fixture','fixture')",(snap,order,name,name))
                    c.commit()
                async def verify():
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                        async def profile(name):
                            response=await client.get('/api/persons/profile',params={'normalized_name':name})
                            self.assertEqual(response.status_code,200)
                            return response.json()
                        expected={'SOLO INTERINO':(5,1,0,0),'SOLO CARRERA':(None,0,1,0),
                                  'MIXTA':(5,1,1,0),'MULTIPLE':(4,2,0,0),
                                  'REPETIDA':(8,2,0,0),'AMBIGUA':(None,0,0,1)}
                        for name,(best,interino,career,unknown) in expected.items():
                            p=await profile(name);s=p['summary']
                            self.assertEqual((s['best_bag_position'],s['total_bag_records'],s['total_career_practice_records'],s['total_unidentified_procedure_records']),(best,interino,career,unknown))
                            self.assertEqual(len(p['bag_memberships']),interino+career+unknown)
                            if name in ('SOLO CARRERA','MIXTA'):
                                self.assertEqual(p['user_view']['current_result'],'no_data')
                                self.assertIsNone(p['user_view']['bag_position'])
                                self.assertTrue(any(x['staff_kind']=='career_practice' and x['order_number']==1 for x in p['bag_memberships']))
                            if name=='AMBIGUA':
                                self.assertEqual(p['user_view']['current_result'],'no_data')
                                self.assertIsNone(p['user_view']['bag_position'])
                        response=await client.get('/api/search/persons',params={'q':'MIXTA'})
                        self.assertEqual(response.status_code,200)
                        item=response.json()['items'][0]
                        self.assertEqual(tuple(int(item[k]) for k in ('total_records','total_bag_records','total_career_practice_records')),(2,1,1))
                asyncio.run(verify())
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as c:
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        self.assertEqual(c.execute('SELECT count(*) n FROM '+table).fetchone()['n'],0)


if __name__=='__main__':unittest.main()

"""Replay PDF 404 in a disposable PostgreSQL instance, with no personal output."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster, ROOT, connection_guards


class Bag404PostgresTests(unittest.TestCase):
    def test_replay_idempotence_and_failure_rollback(self):
        from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService, PARSER_KEY
        from app.services.parse_plan import preview
        from app.services.parse_selection import selection_from_plan, selection_scope
        from app.storage.docent_bag_store import DocentBagStore
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/plan_reprocesado_historico_20260615_v2.json').read_text())
        e=next(x for x in plan['entries'] if x['version_id']==404);path=Path(e['local_sha_verified_paths'][0])
        with FreshCluster() as cluster:
            with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as admin:
                cluster.verify(admin);admin.execute('CREATE DATABASE funk_validation')
            env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
            env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0')
            with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
                list(connection_guards(cluster,stack))
                from app.storage.db import init_db,close_pool
                stack.callback(close_pool);init_db(str(ROOT/'app/storage/schema.sql'))
                with psycopg.connect(cluster.dsn(),row_factory=dict_row) as db:
                    sid=db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('fixture','https://example.invalid','fixture') RETURNING id").fetchone()['id']
                    vid=db.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,%s,%s,%s,%s,now(),'unknown') RETURNING id",(e['sha256'],path.name,path.name,str(path),path.stat().st_size)).fetchone()['id']
                    did=db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,list_scope,title) VALUES(%s,%s,'docent_career_practice_participants','secundaria_otros','fixture') RETURNING id",(vid,sid)).fetchone()['id']
                    db.execute("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at) VALUES(%s,%s,'prior-fixture','success',now())",(vid,PARSER_KEY));db.commit()
                    def plan():return preview(shas=[e['sha256']],parser_key=PARSER_KEY,reprocess=True)
                    def snapshot():
                        db.rollback()
                        return db.execute("""SELECT count(*) n,md5(coalesce(string_agg(
                          ((to_jsonb(m)-ARRAY['id','snapshot_id','created_at']) ||
                           jsonb_build_object('snapshot',to_jsonb(s)-ARRAY['id','created_at']))::text,
                          '|' ORDER BY ((to_jsonb(m)-ARRAY['id','snapshot_id','created_at']) ||
                           jsonb_build_object('snapshot',to_jsonb(s)-ARRAY['id','created_at']))::text),'')) h
                          FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s""",(did,)).fetchone()
                    service=DocentBagParticipantsParserService();p=plan()
                    with selection_scope(selection_from_plan(p)):first=service.parse_all_documents()
                    self.assertEqual([(r['status'],r['rows_extracted']) for r in first],[('success',481)])
                    before=dict(snapshot());self.assertEqual(before['n'],481)
                    db.rollback()
                    with selection_scope(selection_from_plan(p)):repeat=service.parse_all_documents()
                    self.assertEqual(repeat,[]);self.assertEqual(dict(snapshot()),before)
                    db.rollback();fresh=plan()
                    with selection_scope(selection_from_plan(fresh)):again=service.parse_all_documents()
                    self.assertEqual(again[0]['status'],'success');self.assertEqual(dict(snapshot()),before)
                    db.rollback();failing=plan();original=DocentBagStore.insert_member;counter=[0]
                    def fail(store,**kwargs):
                        counter[0]+=1
                        if counter[0]==10:raise RuntimeError('synthetic_failure')
                        return original(store,**kwargs)
                    with patch.object(DocentBagStore,'insert_member',fail),selection_scope(selection_from_plan(failing)):
                        failed=service.parse_all_documents()
                    self.assertEqual(failed[0]['status'],'failed');self.assertEqual(dict(snapshot()),before)
                    db.rollback();incomplete=plan()
                    text="\n".join(["PROFESSORS D'ENSENYAMENT SECUNDARI",'ANGLÉS (411)',
                                    '***1234**EXEMPLE, PRIMER 1 PROVISIONALS','****2345*UNPARSEABLE'])
                    with patch.object(service,'_extract_text',return_value=text),selection_scope(selection_from_plan(incomplete)):
                        rejected=service.parse_all_documents()
                    self.assertEqual(rejected[0]['status'],'failed');self.assertEqual(dict(snapshot()),before)
                    for table in ('public_alerts','notification_posts','push_notification_events'):
                        self.assertEqual(db.execute('SELECT count(*) n FROM '+table).fetchone()['n'],0)


if __name__=='__main__':unittest.main()

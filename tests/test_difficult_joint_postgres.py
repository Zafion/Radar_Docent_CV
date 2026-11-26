"""Real local difficult coverage documents in one disposable cluster."""
from contextlib import ExitStack
import json,os,unittest
from pathlib import Path
from unittest.mock import patch
import psycopg
from psycopg.rows import dict_row
from tests.postgres_isolated_validation import FreshCluster,ROOT,connection_guards

class DifficultJointPostgresTest(unittest.TestCase):
 def test_joint_persist_replay_and_failure(self):
  from app.services.difficult_coverage_provisional_parser import DifficultCoverageProvisionalParserService
  from app.services.offered_positions_parser import OfferedPositionsParserService
  from app.services.parse_plan import preview
  from app.services.parse_selection import selection_from_plan,selection_scope
  from app.storage.difficult_coverage_store import DifficultCoverageStore
  entries={e['version_id']:e for e in json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/plan_reprocesado_historico_20260615_v2.json').read_text())['entries'] if e['version_id'] in (673,674,675)}
  with FreshCluster() as cluster:
   with psycopg.connect(cluster.dsn('postgres'),autocommit=True) as c:cluster.verify(c);c.execute('CREATE DATABASE funk_validation')
   env={k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))};env.update(RADAR_DOCENT_DB_URL=cluster.dsn(),ENABLE_NOTIFICATIONS='0')
   with patch.dict(os.environ,env,clear=True),ExitStack() as stack:
    list(connection_guards(cluster,stack))
    from app.storage.db import init_db,close_pool
    stack.callback(close_pool);init_db(str(ROOT/'app/storage/schema.sql'))
    with psycopg.connect(cluster.dsn(),row_factory=dict_row) as db:
     sid=db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('fixture','https://example.invalid','fixture') RETURNING id").fetchone()['id'];ids={}
     for n,e in entries.items():
      path=Path(e['local_sha_verified_paths'][0]);vid=db.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,%s,%s,%s,%s,now(),'unknown') RETURNING id",(e['sha256'],path.name,path.name,str(path),path.stat().st_size)).fetchone()['id']
      ids[n]=db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,list_scope,title) VALUES(%s,%s,%s,'dificil_cobertura','fixture') RETURNING id",(vid,sid,e['family'])).fetchone()['id']
     db.commit()
     def run(n,key,service,plan=None):
      p=plan or preview(shas=[entries[n]['sha256']],parser_key=key,reprocess=True)
      with selection_scope(selection_from_plan(p)):return service().parse_all_documents()
     for n in (673,675):self.assertEqual(run(n,'offered_positions_parser',OfferedPositionsParserService)[0]['status'],'success')
     key='difficult_coverage_provisional_parser';p=preview(shas=[entries[674]['sha256']],parser_key=key,reprocess=True)
     first=run(674,key,DifficultCoverageProvisionalParserService,p);self.assertEqual((first[0]['status'],first[0]['positions_extracted'],first[0]['candidates_extracted']),('success',52,5381))
     self.assertEqual(run(674,key,DifficultCoverageProvisionalParserService,p),[])
     db.rollback()
     def counts():
      db.rollback();return db.execute('SELECT count(*) n FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s',(ids[674],)).fetchone()['n']
     self.assertEqual(counts(),5381)
     for n in (673,675):
      db.rollback();count=db.execute('''SELECT count(*) n FROM difficult_coverage_positions p JOIN offered_positions o ON o.center_code=p.center_code AND o.position_code=p.position_code AND o.document_id=%s WHERE p.document_id=%s''',(ids[n],ids[674])).fetchone()['n'];self.assertEqual(count,52)
     replay=run(674,key,DifficultCoverageProvisionalParserService);self.assertEqual(replay[0]['status'],'success');self.assertEqual(counts(),5381)
     original=DifficultCoverageStore.insert_candidate;calls=[0]
     def fail(store,**kwargs):
      calls[0]+=1
      if calls[0]==10:raise RuntimeError('synthetic failure')
      return original(store,**kwargs)
     with patch.object(DifficultCoverageStore,'insert_candidate',fail):failed=run(674,key,DifficultCoverageProvisionalParserService)
     self.assertEqual(failed[0]['status'],'failed');self.assertEqual(counts(),5381)
     for table in ('public_alerts','notification_posts','push_notification_events'):
      self.assertEqual(db.execute('SELECT count(*) n FROM '+table).fetchone()['n'],0)
if __name__=='__main__':unittest.main()

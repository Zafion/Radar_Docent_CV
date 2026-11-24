"""Incremental rehearsal in a fresh clone; unaccredited data remains quarantined."""
import asyncio,os
import httpx
import psycopg
from psycopg.types.json import Jsonb

SHA_ROWS={'a5dbb6bae2fe719486e44c70a493cab6d790ee1bb59055272d90e1bd9fad69eb':56,
          '08ba041d65fe397d6d005a877ad15053058450cd28768634a63dde0c845e3b1b':2}
TABLES=('documents','document_versions','award_results','award_assignments','offered_positions',
        'docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates',
        'public_alerts','notification_posts','push_notification_events')

def counts(db):return {t:db.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in TABLES}

def check_api(socket):
    kwargs={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5,'dbname':'baseline_trial_20261123',
            'options':'-c default_transaction_read_only=on'}
    with psycopg.connect(**kwargs) as db:
        bag=db.execute("SELECT person_name_normalized FROM docent_bag_members WHERE person_name_normalized IS NOT NULL LIMIT 1").fetchone()
        award=db.execute("SELECT person_name_normalized FROM award_results WHERE person_name_normalized IS NOT NULL LIMIT 1").fetchone()
    os.environ['RADAR_DOCENT_DB_URL']=psycopg.conninfo.make_conninfo(**kwargs);os.environ['ENABLE_NOTIFICATIONS']='0'
    from app.api.main import app
    from app.storage.db import close_pool,get_pool
    with get_pool().connection() as db:
        ident=db.execute("SELECT current_database() AS name,current_setting('data_directory') AS data,current_setting('transaction_read_only') AS ro").fetchone()
        if ident['name']!='baseline_trial_20261123' or ident['data']!='/work/data' or ident['ro']!='on':raise ValueError('api_database_identity')
    async def checks():
        requests=[('search','/api/search/persons',{'q':bag[0] if bag else 'abc','limit':1}),
          ('bag_profile','/api/persons/profile',{'normalized_name':bag[0] if bag else 'abc'}),
          ('award_profile','/api/persons/profile',{'normalized_name':award[0] if award else 'abc'}),
          ('awards','/api/awards',{'limit':1}),('positions','/api/offered-positions',{'limit':1}),
          ('centers','/api/centers',{'limit':1}),('procedures','/api/historical-procedures',{})]
        result={}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
            for label,url,params in requests:
                try:
                    r=await client.get(url,params=params);entry={'http_status':r.status_code}
                    if r.status_code==200:
                        data=r.json()
                        if isinstance(data,dict):
                            for key in ('items','bag_memberships','awards'):
                                if isinstance(data.get(key),list):entry[key+'_count']=len(data[key])
                    result[label]=entry
                except Exception as e:result[label]={'error_type':type(e).__name__}
        return result
    try:return {'database_identity_verified':True,'read_only':True,'endpoints':asyncio.run(checks())}
    finally:close_pool()

def run(old,new,socket):
    original=counts(old);old.rollback();old.close()
    kwargs={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kwargs,dbname='postgres',autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname='baseline_trial_20261123'").fetchone():raise ValueError('trial_already_exists')
        admin.execute('CREATE DATABASE baseline_trial_20261123 TEMPLATE baseline')
    new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    rows=new.execute('''SELECT v.sha256,p.id,to_jsonb(p)-'id'-'document_id'-'created_at'
      FROM offered_positions p JOIN documents d ON d.id=p.document_id
      JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=ANY(%s) ORDER BY v.sha256,p.id''',(list(SHA_ROWS),)).fetchall()
    for sha,n in SHA_ROWS.items():
        if sum(r[0]==sha for r in rows)!=n:raise ValueError('validated_document_row_count_changed')
    matches=new.execute('''SELECT a.id,ar.id,op.id,dv.sha256,ov.sha256,a.position_code,a.center_code,
      COALESCE(a.position_specialty_code,ar.specialty_code),a.position_code=op.position_code,
      a.center_code=op.center_code,COALESCE(a.position_specialty_code,ar.specialty_code)=op.specialty_code,
      (ar.body_code IS NULL OR op.body_code IS NULL OR ar.body_code=op.body_code)
      FROM award_assignments a JOIN award_results ar ON ar.id=a.award_result_id
      JOIN documents ad ON ad.id=ar.document_id JOIN document_versions dv ON dv.id=ad.document_version_id
      JOIN offered_positions op ON op.id=a.matched_offered_position_id
      JOIN documents od ON od.id=op.document_id JOIN document_versions ov ON ov.id=od.document_version_id
      JOIN historical_procedure_documents ap ON ap.document_id=ad.id AND ap.procedure_key='interino-2026-11-03'
      JOIN historical_procedure_documents pp ON pp.document_id=od.id AND pp.procedure_key='interino-2026-11-03'
      WHERE a.matched_offered_position_id IS NOT NULL''').fetchall()
    if len(matches)!=213 or len({r[0] for r in matches})!=213 or any(not all(r[8:12]) for r in matches):raise ValueError('strict_associations_not_verified')
    new.rollback()
    with psycopg.connect(**kwargs,dbname='baseline_trial_20261123') as trial:
        if counts(trial)!=original:raise ValueError('clone_counts_differ')
        trial.execute('''CREATE SCHEMA recovery_quarantine;
          CREATE TABLE recovery_quarantine.positions (source_sha text NOT NULL,source_position_id bigint NOT NULL,
          document_id bigint NOT NULL REFERENCES public.documents(id), payload jsonb NOT NULL,
          historical_status text NOT NULL CHECK(historical_status='unknown'), PRIMARY KEY(source_sha,source_position_id));
          CREATE TABLE recovery_quarantine.strict_match_evidence(source_assignment_id bigint PRIMARY KEY,source_result_id bigint NOT NULL,
          source_offer_id bigint NOT NULL,award_sha text NOT NULL,offer_sha text NOT NULL,position_code text NOT NULL,
          center_code text NOT NULL,specialty_code text NOT NULL,procedure_key text NOT NULL,
          promotion text NOT NULL CHECK(promotion='not_promoted'));''')
        trial.commit()
        insert='''INSERT INTO recovery_quarantine.positions SELECT %s,%s,d.id,%s,'unknown' FROM documents d
          JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s
          ON CONFLICT(source_sha,source_position_id) DO NOTHING'''
        def apply():
            changed=0
            for sha,pid,payload in rows:changed+=trial.execute(insert,(sha,pid,Jsonb(payload),sha)).rowcount
            for r in matches:
                changed+=trial.execute('''INSERT INTO recovery_quarantine.strict_match_evidence VALUES
                    (%s,%s,%s,%s,%s,%s,%s,%s,'interino-2026-11-03','not_promoted') ON CONFLICT DO NOTHING''',r[:8]).rowcount
            return changed
        first=apply();trial.commit()
        second=apply();trial.commit()
        trial.execute('SAVEPOINT rollback_check')
        trial.execute("DELETE FROM recovery_quarantine.positions WHERE source_position_id=(SELECT min(source_position_id) FROM recovery_quarantine.positions)")
        trial.execute('ROLLBACK TO SAVEPOINT rollback_check')
        staged=trial.execute('SELECT count(*) FROM recovery_quarantine.positions').fetchone()[0]
        staged_matches=trial.execute('SELECT count(*) FROM recovery_quarantine.strict_match_evidence').fetchone()[0]
        after=counts(trial)
        if first!=271 or second!=0 or staged!=58 or staged_matches!=213 or after!=original:raise ValueError('trial_integrity_failed')
        trial.commit()
    # API reads only public baseline data; quarantine is deliberately invisible.
    os.environ['RADAR_DOCENT_DB_URL']=psycopg.conninfo.make_conninfo(**kwargs,dbname='baseline_trial_20261123',options='-c default_transaction_read_only=on')
    os.environ['ENABLE_NOTIFICATIONS']='0'
    from app.api.main import app
    from app.storage.db import close_pool
    async def checks():
        results={}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
            for label,url,params in [('search','/api/search/persons',{'q':'abc','limit':1}),
                ('awards','/api/awards',{'limit':1}),('positions','/api/offered-positions',{'limit':1}),
                ('centers','/api/centers',{'limit':1}),('procedures','/api/historical-procedures',{})]:
                try:r=await client.get(url,params=params);results[label]={'http_status':r.status_code}
                except Exception as e:results[label]={'error_type':type(e).__name__}
        return results
    try:api=asyncio.run(checks())
    finally:close_pool()
    return {'status':'QUARANTINE_REHEARSAL','trial_database':'baseline_trial_20261123','baseline_core_counts':original,
            'trial_core_counts':after,'quarantined_validated_position_rows':staged,'strict_match_evidence_rows':staged_matches,
            'business_associations_imported':0,'first_insertions':first,'second_insertions':second,
            'rollback_verified':True,'baseline_rows_removed':0,'notifications_changed':False,'api':api,
            'limits':['Positions have unknown publication date and remain outside application tables.',
                      'Match evidence references source reconstruction identifiers; associated business rows were not imported.',
                      'No unverified extraction was promoted.']}

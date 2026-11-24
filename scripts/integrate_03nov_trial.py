"""Transactional, provenance-preserving 03-Nov integration in trial only."""
from collections import Counter
import asyncio,os
import json,hashlib
from pathlib import Path
import psycopg
import httpx
from psycopg import sql
from psycopg.rows import dict_row

ROOT=Path(__file__).resolve().parents[1]
SHAS={700:'e9647ae9d20db8f1d95e1eafcd712f6c365ba643e1447f9d58da5699db7f1c04',
      701:'847dcc1233a91b5694d1738f75ddd09e536d6764638ba87e89ab7f714510b348',
      702:'33e69ded5cd9c7ef44032e749d1893ca7bd2717fca0ad99c864e7708992693b4'}
CORE=('documents','document_versions','award_results','award_assignments','offered_positions','centers',
      'public_alerts','notification_posts','push_notification_events')

def counts(db):return {t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in CORE}
def cols(db,table):
    return [r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,))]

def insert_row(db,table,data):
    columns=list(data)
    return db.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({}) RETURNING id').format(
        sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,columns)),sql.SQL(',').join(sql.Placeholder()*len(columns))),
        [data[c] for c in columns]).fetchone()['id']

def run(new,socket):
    snapshot_path=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/inventario_hetzner_snapshot_v2.json'
    raw=snapshot_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='95263b363a91c6e6083ef8ae95cd3bebb8fd36773a53cad6a416785ddd2126c5':raise ValueError('inventory_identity_mismatch')
    snap=json.loads(raw);official={}
    for row in snap['versions']:
        if row.get('version_id') not in SHAS:continue
        ver=row['version_id']
        if row.get('sha256')!=SHAS[ver]:raise ValueError('inventory_document_sha_mismatch')
        found=[a for a in row['assets'] if a.get('source_key')=='rrhh_main' and a.get('source_url')=='https://ceice.gva.es/es/web/rrhh-educacion'
               and a.get('canonical_url','').startswith('https://ceice.gva.es/documents/')]
        if len(found)!=1:raise ValueError('official_source_identity_missing')
        official[ver]=found[0]
    if set(official)!=set(SHAS):raise ValueError('inventory_document_missing')
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname='baseline_trial_pre03_20261123'").fetchone():
            with psycopg.connect(**kw,dbname='baseline_trial_20261123',row_factory=dict_row) as check:
                has_map=check.execute("SELECT to_regclass('recovery_03nov.document_map') AS t").fetchone()['t']
                if has_map:
                    docs=check.execute('SELECT source_sha,target_document_id FROM recovery_03nov.document_map').fetchall()
                    if {d['source_sha'] for d in docs}!=set(SHAS.values()):raise ValueError('replay_document_identity_mismatch')
                    matched=check.execute('''SELECT count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                        WHERE r.document_id IN (SELECT target_document_id FROM recovery_03nov.document_map) AND a.matched_offered_position_id IS NOT NULL''').fetchone()['n']
                    if matched!=213:raise ValueError('replay_match_count_mismatch')
                    return {'status':'INTEGRATION_REPLAY_NOOP','documents':3,'associations':matched,'inserted':0}
                with psycopg.connect(**kw,dbname='baseline_trial_pre03_20261123',row_factory=dict_row) as backup:
                    if counts(check)!=counts(backup):raise ValueError('trial_changed_after_failed_attempt')
        else:admin.execute('CREATE DATABASE baseline_trial_pre03_20261123 TEMPLATE baseline_trial_20261123')
    new.row_factory=dict_row
    new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    source_docs={}
    for ver,sha in SHAS.items():
        row=new.execute('''SELECT d.id AS document_id,d.document_version_id,d.source_id,d.doc_family,d.validation_status,d.historical_status,
            d.publication_date_quality,s.source_key,s.source_url FROM documents d JOIN document_versions v ON v.id=d.document_version_id
            JOIN sources s ON s.id=d.source_id WHERE v.sha256=%s''',(sha,)).fetchone()
        if not row:raise ValueError('source_document_missing')
        source_docs[ver]=row
    expected={700:6416,701:11024,702:210}
    for ver,row in source_docs.items():
        table='offered_positions' if ver==702 else 'award_results'
        if new.execute('SELECT count(*) AS n FROM '+table+' WHERE document_id=%s',(row['document_id'],)).fetchone()['n']!=expected[ver]:raise ValueError('source_document_count_mismatch')
    with psycopg.connect(**kw,dbname='baseline_trial_20261123',row_factory=dict_row) as dst:
        identity=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS listen").fetchone()
        if identity!={'db':'baseline_trial_20261123','data':'/work/data','listen':''}:raise ValueError('trial_identity_mismatch')
        before=counts(dst);quarantine=dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions').fetchone()['n']
        if quarantine!=58:raise ValueError('quarantine_changed')
        dst.commit()
        with dst.transaction():
            dst.execute((ROOT/'app/storage/migrations/20261122_document_validation_metadata.sql').read_text())
            dst.execute((ROOT/'app/storage/migrations/20261122_historical_procedures.sql').read_text())
            dst.execute('''CREATE SCHEMA IF NOT EXISTS recovery_03nov;
                CREATE TABLE IF NOT EXISTS recovery_03nov.document_map(source_sha text PRIMARY KEY,target_document_id bigint NOT NULL UNIQUE REFERENCES documents(id));
                CREATE TABLE IF NOT EXISTS recovery_03nov.row_map(kind text NOT NULL,source_id bigint NOT NULL,target_id bigint NOT NULL,PRIMARY KEY(kind,source_id),UNIQUE(kind,target_id));''')
            if dst.execute('SELECT count(*) AS n FROM recovery_03nov.document_map').fetchone()['n']:raise ValueError('integration_already_started')
            fieldmap={t:[c for c in cols(new,t) if c in set(cols(dst,t)) and c!='id'] for t in ('document_versions','documents','document_parse_runs','award_results','award_assignments','offered_positions')}
            docmap={};versionmap={};runmap={}
            for ver,sha in SHAS.items():
                src=source_docs[ver];s=official[ver]
                target=dst.execute('SELECT id,source_url FROM sources WHERE source_key=%s',(s['source_key'],)).fetchone()
                if not target or target['source_url']!=s['source_url']:raise ValueError('official_source_identity_conflict')
                if dst.execute('SELECT 1 FROM document_versions WHERE sha256=%s',(sha,)).fetchone():raise ValueError('sha_already_exists_requires_review')
                v=new.execute('SELECT * FROM document_versions WHERE id=%s',(src['document_version_id'],)).fetchone()
                versionmap[ver]=insert_row(dst,'document_versions',{c:v[c] for c in fieldmap['document_versions']})
                pr=new.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(src['document_version_id'],)).fetchone()
                if not pr or pr['rows_extracted']!=expected[ver]:raise ValueError('source_parse_run_mismatch')
                p={c:pr[c] for c in fieldmap['document_parse_runs']};p['document_version_id']=versionmap[ver]
                runmap[ver]=insert_row(dst,'document_parse_runs',p)
                d=new.execute('SELECT * FROM documents WHERE id=%s',(src['document_id'],)).fetchone()
                data={c:d[c] for c in fieldmap['documents']};data.update(document_version_id=versionmap[ver],source_id=target['id'],
                    validation_status='needs_validation',validation_parse_run_id=runmap[ver],historical_status='unknown',publication_date_quality='unknown')
                docmap[ver]=insert_row(dst,'documents',data)
                dst.execute('INSERT INTO recovery_03nov.document_map VALUES(%s,%s)',(sha,docmap[ver]))
            offers={};results={};totals=Counter()
            def copy_source(table,ver,mapping):
                with new.cursor(name='src_'+table+'_'+str(ver),row_factory=dict_row) as cur:
                    cur.execute('SELECT * FROM '+table+' WHERE document_id=%s ORDER BY id',(source_docs[ver]['document_id'],))
                    while batch:=cur.fetchmany(500):
                        for row in batch:
                            data={c:row[c] for c in fieldmap[table]};data['document_id']=docmap[ver]
                            target_id=insert_row(dst,table,data);mapping[row['id']]=target_id
                            dst.execute('INSERT INTO recovery_03nov.row_map VALUES(%s,%s,%s)',(table,row['id'],target_id));totals[table]+=1
            copy_source('offered_positions',702,offers)
            for ver in (700,701):copy_source('award_results',ver,results)
            with new.cursor(name='src_assignments',row_factory=dict_row) as cur:
                cur.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                    WHERE r.document_id=ANY(%s) ORDER BY a.id''',([source_docs[v]['document_id'] for v in (700,701)],))
                while batch:=cur.fetchmany(500):
                    for row in batch:
                        data={c:row[c] for c in fieldmap['award_assignments']};data['award_result_id']=results[row['award_result_id']]
                        match=row.get('matched_offered_position_id')
                        if match is not None and match not in offers:raise ValueError('assignment_offer_outside_procedure')
                        data['matched_offered_position_id']=offers.get(match)
                        aid=insert_row(dst,'award_assignments',data)
                        dst.execute('INSERT INTO recovery_03nov.row_map VALUES(%s,%s,%s)',('award_assignments',row['id'],aid))
                        totals['award_assignments']+=1;totals['associations']+=int(match is not None)
            if totals['award_results']!=17440 or totals['offered_positions']!=210 or totals['associations']!=213:raise ValueError('integration_count_mismatch')
            check=dst.execute('''SELECT count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                JOIN offered_positions p ON p.id=a.matched_offered_position_id WHERE r.document_id=ANY(%s)
                AND a.position_code=p.position_code AND a.center_code=p.center_code
                AND COALESCE(a.position_specialty_code,r.specialty_code)=p.specialty_code
                AND (r.body_code IS NULL OR p.body_code IS NULL OR r.body_code=p.body_code)''',([docmap[700],docmap[701]],)).fetchone()['n']
            if check!=213:raise ValueError('target_association_conflict')
            procedure=new.execute("SELECT * FROM historical_procedures WHERE procedure_key='interino-2026-11-03'").fetchone()
            if not procedure or procedure['publication_date'] is not None or procedure['publication_date_quality']!='unknown':raise ValueError('procedure_publication_date_conflict')
            dst.execute('INSERT INTO historical_procedures(procedure_key,procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality) VALUES(%s,%s,%s,%s,%s,%s)',
                tuple(procedure[x] for x in ('procedure_key','procedure_kind','procedure_date','date_evidence','publication_date','publication_date_quality')))
            for ver in (700,701,702):
                link=new.execute("SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03' AND document_id=%s",(source_docs[ver]['document_id'],)).fetchone()
                if not link:raise ValueError('procedure_document_evidence_missing')
                dst.execute('INSERT INTO historical_procedure_documents VALUES(%s,%s,%s,%s)',('interino-2026-11-03',docmap[ver],link['document_role'],link['relationship_evidence']))
            after=counts(dst)
            if any(after[t]!=before[t] for t in ('centers','public_alerts','notification_posts','push_notification_events')):raise ValueError('unrelated_data_changed')
            if dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions').fetchone()['n']!=58:raise ValueError('quarantine_changed')
            with dst.transaction(force_rollback=True):
                dst.execute('DELETE FROM historical_procedure_documents WHERE procedure_key=%s',('interino-2026-11-03',))
            if dst.execute('SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key=%s',('interino-2026-11-03',)).fetchone()['n']!=3:raise ValueError('rollback_failed')
        # Same identity and mapping permit a second no-op, never reinsert data.
        if dst.execute('SELECT count(*) AS n FROM recovery_03nov.document_map').fetchone()['n']!=3:raise ValueError('replay_mapping_invalid')
        repeat=counts(dst)
        if repeat!=after:raise ValueError('replay_not_idempotent')
        dst.rollback()
    new.rollback()
    return {'status':'INTEGRATED_FUNCTIONAL_TRIAL','before':before,'after':after,'inserted':dict(totals),'documents':3,
      'quarantined_positions':58,'backup_database':'baseline_trial_pre03_20261123','document_mapping':'source SHA to destination IDs',
      'replay':'mapping_checked_noop','rollback_verified':True,'notifications':'unchanged_existing_baseline_rows',
      'publication_date_quality':'unknown','validation_status':'needs_validation','historical_status':'unknown',
      'limits':['Replay checks the integration mapping; no reinsert of mapped documents.',
                'Existing centers were not modified; code and name are preserved in document rows.']}

def check_api(new,socket):
    new.row_factory=dict_row
    sample=new.execute('''SELECT r.person_name_normalized,p.center_code,p.specialty_code FROM award_results r JOIN award_assignments a ON a.award_result_id=r.id
       JOIN offered_positions p ON p.id=a.matched_offered_position_id JOIN documents d ON d.id=r.document_id
       JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=ANY(%s) LIMIT 1''',([SHAS[700],SHAS[701]],)).fetchone()
    new.rollback()
    if not sample:raise ValueError('source_linked_person_missing')
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5,'dbname':'baseline_trial_20261123',
        'options':'-c default_transaction_read_only=on'}
    with psycopg.connect(**kw,row_factory=dict_row) as check:
        catalog=check.execute('''SELECT p.center_code FROM offered_positions p JOIN centers c ON c.center_code=p.center_code
           JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s LIMIT 1''',(SHAS[702],)).fetchone()
        if not catalog:raise ValueError('integrated_center_not_in_catalog')
    os.environ['RADAR_DOCENT_DB_URL']=psycopg.conninfo.make_conninfo(**kw);os.environ['ENABLE_NOTIFICATIONS']='0'
    from app.api.main import app
    from app.storage.db import close_pool,get_pool
    with get_pool().connection() as db:
        ident=db.execute("SELECT current_database() AS name,current_setting('data_directory') AS data,current_setting('transaction_read_only') AS ro").fetchone()
        if ident!={'name':'baseline_trial_20261123','data':'/work/data','ro':'on'}:raise ValueError('api_identity_mismatch')
    async def checks():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
            response=await client.get('/api/historical-procedures/interino-2026-11-03')
            if response.status_code!=200:raise ValueError('procedure_endpoint_failed')
            obj=response.json();docs=obj['documents'];proc=obj['procedure']
            if len(docs)!=3 or {d['source_document_sha256'] for d in docs}!=set(SHAS.values()):raise ValueError('procedure_document_response_mismatch')
            if any(not str(d.get('official_document_url','')).startswith('https://ceice.gva.es/documents/') or d.get('document_url_evidence')!='verified_inventory_reference' for d in docs):raise ValueError('procedure_source_url_mismatch')
            if {d['source_document_sha256']:d['extracted_rows'] for d in docs}!={SHAS[700]:6416,SHAS[701]:11024,SHAS[702]:210}:raise ValueError('procedure_document_count_mismatch')
            if any(d.get('validation_status')!='needs_validation' or d.get('historical_status')!='unknown' for d in docs):raise ValueError('api_validation_status_mismatch')
            if proc['publication_date'] is not None or proc['publication_date_quality']!='unknown':raise ValueError('api_publication_date_mismatch')
            dpos=next(d for d in docs if d['source_document_sha256']==SHAS[702]);pid=dpos['document_id']
            offers=await client.get('/api/offered-positions',params={'document_id':pid,'include_unavailable':'true','limit':1})
            if offers.status_code!=200 or offers.json().get('total')!=210:raise ValueError('offered_positions_api_mismatch')
            person=sample['person_name_normalized']
            search=await client.get('/api/search/persons',params={'q':person,'limit':5})
            profile=await client.get('/api/persons/profile',params={'normalized_name':person})
            if search.status_code!=200 or profile.status_code!=200:raise ValueError('person_api_failed')
            if not search.json().get('items'):raise ValueError('person_search_empty')
            related=[x for x in profile.json().get('awards',[]) if x.get('source_document_sha256') in {SHAS[700],SHAS[701]} and x.get('matched_assignments_count')]
            if not related:raise ValueError('person_profile_missing_association')
            centers=await client.get('/api/centers',params={'limit':1})
            if centers.status_code!=200:raise ValueError('center_api_failed')
            detail=await client.get('/api/centers/'+catalog['center_code'])
            if detail.status_code!=200 or detail.json().get('center',{}).get('center_code')!=catalog['center_code']:raise ValueError('integrated_center_detail_mismatch')
            center_offers=await client.get('/api/offered-positions',params={'document_id':pid,'center_code':sample['center_code'],'include_unavailable':'true','limit':100})
            if center_offers.status_code!=200 or not any(x.get('center_code')==sample['center_code'] and x.get('specialty_code')==sample['specialty_code'] for x in center_offers.json().get('items',[])):
                raise ValueError('center_specialty_api_mismatch')
            return {'procedure_http':200,'procedure_documents':len(docs),'offered_positions':210,
                    'search_http':search.status_code,'profile_http':profile.status_code,
                    'profile_has_matched_award':True,'centers_http':centers.status_code,
                    'center_and_specialty_verified':True,'integrated_center_detail_verified':True,'document_result_counts':[6416,11024,210],
                    'source_shas':sorted(SHAS.values()),'publication_date_quality':proc['publication_date_quality'],
                    'validation_statuses':sorted({d.get('validation_status') for d in docs}),
                    'historical_statuses':sorted({d.get('historical_status') for d in docs})}
    try:return asyncio.run(checks())
    finally:close_pool()

def ensure_document_evidence(socket):
    path=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/inventario_hetzner_snapshot_v2.json'
    raw=path.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    if digest!='95263b363a91c6e6083ef8ae95cd3bebb8fd36773a53cad6a416785ddd2126c5':raise ValueError('inventory_identity_mismatch')
    records=[]
    for entry in json.loads(raw)['versions']:
        ver=entry.get('version_id')
        if ver not in SHAS:continue
        assets=[x for x in entry['assets'] if x.get('source_key')=='rrhh_main' and x.get('source_url')=='https://ceice.gva.es/es/web/rrhh-educacion']
        if entry.get('sha256')!=SHAS[ver] or len(assets)!=1:raise ValueError('document_evidence_mismatch')
        records.append((SHAS[ver],assets[0]['canonical_url'],assets[0]['source_key'],digest))
    if len(records)!=3:raise ValueError('document_evidence_missing')
    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname='baseline_trial_20261123',row_factory=dict_row) as db:
        with db.transaction():
            db.execute('''CREATE TABLE IF NOT EXISTS recovery_03nov.document_evidence(source_sha text PRIMARY KEY REFERENCES recovery_03nov.document_map(source_sha),
                canonical_url text NOT NULL,source_key text NOT NULL,inventory_sha256 text NOT NULL,publication_date_quality text NOT NULL CHECK(publication_date_quality='unknown'))''')
            inserted=0
            for sha,url,key,inventory in records:
                inserted+=db.execute("INSERT INTO recovery_03nov.document_evidence VALUES(%s,%s,%s,%s,'unknown') ON CONFLICT DO NOTHING",(sha,url,key,inventory)).rowcount
                actual=db.execute('SELECT canonical_url,source_key,inventory_sha256 FROM recovery_03nov.document_evidence WHERE source_sha=%s',(sha,)).fetchone()
                if actual!={'canonical_url':url,'source_key':key,'inventory_sha256':inventory}:raise ValueError('document_evidence_conflict')
        return {'verified':len(records),'inserted':inserted,'quality':'verified_inventory_reference_not_capture','publication_date':'unknown'}

def verify_integrity(socket):
    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname='baseline_trial_20261123',row_factory=dict_row) as db:
        before=counts(db);db.commit()
        try:
            with db.transaction():
                db.execute("DELETE FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'")
                raise RuntimeError('synthetic_failure_after_write')
        except RuntimeError as exc:
            if str(exc)!='synthetic_failure_after_write':raise
        if db.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-03'").fetchone()['n']!=3:raise ValueError('failure_rollback_mismatch')
        rows=db.execute('''SELECT v.sha256,count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
          JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id
          JOIN offered_positions p ON p.id=a.matched_offered_position_id JOIN documents od ON od.id=p.document_id
          JOIN document_versions ov ON ov.id=od.document_version_id WHERE v.sha256=ANY(%s) AND ov.sha256=%s
          AND a.position_code=p.position_code AND a.center_code=p.center_code
          AND COALESCE(a.position_specialty_code,r.specialty_code)=p.specialty_code
          AND (r.body_code IS NULL OR p.body_code IS NULL OR r.body_code=p.body_code) GROUP BY v.sha256''',
          ([SHAS[700],SHAS[701]],SHAS[702])).fetchall()
        by_sha={r['sha256']:r['n'] for r in rows}
        if by_sha!={SHAS[700]:93,SHAS[701]:120}:raise ValueError('association_integrity_mismatch')
        invalid=db.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()['n']
        if invalid:raise ValueError('foreign_key_validation_missing')
        after=counts(db)
        if before!=after:raise ValueError('failure_rollback_changed_counts')
        centers=db.execute('''SELECT count(DISTINCT p.center_code) AS all_codes,
          count(DISTINCT c.center_code) AS catalog_codes FROM offered_positions p
          JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id
          LEFT JOIN centers c ON c.center_code=p.center_code WHERE v.sha256=%s''',(SHAS[702],)).fetchone()
        return {'error_injected_after_write':True,'rollback_preserved_documents':True,'association_counts':[93,120],
                'foreign_keys_unvalidated':0,'core_counts_unchanged':True,'position_center_codes':dict(centers)}

"""Integrate two evidence-backed procedures exclusively into the owned trial."""
import hashlib,json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from app.services.discovery.verified_evidence import verify_html_link
from scripts.integrate_03nov_trial import ROOT,SHAS as NOV03,cols,counts,insert_row

VERSIONS={1000391:'a8077f5846dcd0a122a8d80928934d8dd851dd0cb9dee0dbb1559d8d06132057',
 1000067:'172e44dbdfff2b82adfb07c092f8d8da1c2c084e97e27a3083e097f310043b21',
 1000444:'c68268513b7e66eb4de63ad582110bdafb15f7c2ea6c8a63d50978c1eab0961a',
 1000005:'01b45eeb0a1aead5a1375e173ddc05866616fff0aac111f5d8eb8e667e5ab14a',
 1000535:'f73e82b00f4bf26e17281d11bc303b2acc8452484e6788c243c2785af94dab05'}
EXPECTED={1000391:6,1000067:6028,1000444:10659,1000005:299,1000535:299}
NOV19=(1000067,1000444,1000005,1000535)

def evidence():
    plan=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json'
    entries={x['version_id']:x for x in json.loads(plan.read_text())['entries'] if x['version_id'] in VERSIONS}
    if set(entries)!=set(VERSIONS):raise ValueError('source_plan_incomplete')
    result={}
    for ver,sha in VERSIONS.items():
        entry=entries[ver];pdf=Path(entry['local_sha_verified_paths'][0]);provenance=entry['provenance']
        if pdf.is_symlink() or entry['sha256']!=sha or hashlib.sha256(pdf.read_bytes()).hexdigest()!=sha:raise ValueError('source_pdf_sha_mismatch')
        if len(provenance['urls'])!=1:raise ValueError('source_url_ambiguous')
        url=provenance['urls'][0];verified=[]
        for group in provenance['evidence']:
            for item in group:
                try:
                    capture=ROOT/item['path']
                    if not capture.resolve().is_relative_to(ROOT.resolve()) or capture.is_symlink():continue
                    verify_html_link(html_bytes=capture.read_bytes(),expected_html_sha256=item['sha256'],final_page_url=item['final_url'],linked_url=url)
                    verified.append(item)
                except (ValueError,OSError,KeyError):continue
        if not verified:raise ValueError('html_link_evidence_missing')
        result[ver]={'sha':sha,'url':url,'html_sha':verified[0]['sha256'],'page_url':verified[0]['final_url']}
    return result

def run(new,socket,*,baseline17=False,target_db=None):
    target_db=target_db or ('baseline17_trial' if baseline17 else 'baseline_trial_20261123')
    if target_db not in (('baseline17_trial','baseline17_reproduction','baseline17_fresh') if baseline17 else ('baseline_trial_20261123',)):raise ValueError('target_identity')
    backup_db=({'baseline17_fresh':'baseline17_fresh_pre0519','baseline17_reproduction':'baseline17_reproduction_pre0519'}.get(target_db,'baseline17_trial_pre0519')) if baseline17 else 'baseline_trial_pre0519_20261123'
    ev=evidence();kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(backup_db,)).fetchone():
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(backup_db),sql.Identifier(target_db)))
    new.row_factory=dict_row;new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    docs={}
    for ver,sha in VERSIONS.items():
        row=new.execute('''SELECT d.id,d.document_version_id,d.doc_family FROM documents d JOIN document_versions v
          ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchone()
        if not row:raise ValueError('source_document_missing')
        table='historical_unawarded_expert_vacancies' if ver==1000391 else 'award_results' if ver in (1000067,1000444) else 'offered_positions'
        if new.execute('SELECT count(*) AS n FROM '+table+' WHERE document_id=%s',(row['id'],)).fetchone()['n']!=EXPECTED[ver]:raise ValueError('source_row_count_mismatch')
        docs[ver]=row
    with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
        ident=dst.execute("SELECT current_database() AS n,current_setting('data_directory') AS d,current_setting('listen_addresses') AS l").fetchone()
        if ident!={'n':target_db,'d':'/work/data','l':''}:raise ValueError('trial_identity_mismatch')
        existing=dst.execute("SELECT to_regclass('recovery_0519.document_map') AS n").fetchone()['n']
        if existing:
            rows=dst.execute('SELECT source_sha FROM recovery_0519.document_map').fetchall()
            if {x['source_sha'] for x in rows}!=set(VERSIONS.values()):raise ValueError('replay_identity_mismatch')
            for ver in (1000067,1000444):
                n=dst.execute('''SELECT count(*) AS n FROM award_results r JOIN documents d ON d.id=r.document_id
                    JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(VERSIONS[ver],)).fetchone()['n']
                if n!=EXPECTED[ver]:raise ValueError('replay_award_count_mismatch')
            linked=dst.execute('''SELECT count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                JOIN recovery_0519.document_map m ON m.target_document_id=r.document_id
                WHERE a.matched_offered_position_id IS NOT NULL''').fetchone()['n']
            if linked:raise ValueError('replay_unaccredited_associations')
            if dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n']!=598:raise ValueError('replay_quarantine_mismatch')
            if dst.execute("SELECT count(*) AS n FROM historical_unawarded_expert_vacancies WHERE result_status='unawarded_no_requests'").fetchone()['n']!=6:raise ValueError('replay_expert_mismatch')
            if dst.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-19'").fetchone()['n']!=4:raise ValueError('replay_procedure_mismatch')
            return {'status':'INTEGRATION_0519_REPLAY_NOOP','documents':5,'inserted':0}
        before=counts(dst);qbefore=dst.execute('SELECT count(*) AS n FROM recovery_17_review.documents' if baseline17 else 'SELECT count(*) AS n FROM recovery_quarantine.positions').fetchone()['n'];dst.commit()
        if not baseline17 and qbefore!=58:raise ValueError('prior_quarantine_changed')
        with psycopg.connect(**kw,dbname=backup_db,row_factory=dict_row) as backup:
            if counts(backup)!=before:raise ValueError('backup_does_not_match_trial')
        with dst.transaction():
            dst.execute('''CREATE SCHEMA IF NOT EXISTS recovery_quarantine;
              CREATE SCHEMA recovery_0519;
              CREATE TABLE recovery_0519.document_map(source_sha text PRIMARY KEY,target_document_id bigint NOT NULL UNIQUE REFERENCES documents(id),
                canonical_url text NOT NULL,page_url text NOT NULL,html_sha text NOT NULL,evidence_quality text NOT NULL);
              CREATE TABLE recovery_0519.row_map(kind text NOT NULL,source_id bigint NOT NULL,target_id bigint NOT NULL,PRIMARY KEY(kind,source_id),UNIQUE(kind,target_id));
              CREATE TABLE recovery_quarantine.positions_19nov(source_sha text NOT NULL,source_id bigint NOT NULL,document_id bigint NOT NULL REFERENCES documents(id),
                payload jsonb NOT NULL,stage_label text NOT NULL,PRIMARY KEY(source_sha,source_id));''')
            source=dst.execute("SELECT id,source_url FROM sources WHERE source_key='rrhh_main'").fetchone()
            if not source or source['source_url']!='https://ceice.gva.es/es/web/rrhh-educacion':raise ValueError('official_source_mismatch')
            fieldmap={t:[c for c in cols(new,t) if c in set(cols(dst,t)) and c!='id'] for t in ('document_versions','documents','document_parse_runs','award_results','award_assignments')}
            docmap={};maps={};totals={}
            for ver in VERSIONS:
                src=docs[ver];sha=VERSIONS[ver]
                existing_doc=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
                if existing_doc:
                    if not baseline17 or ver!=1000391:raise ValueError('document_preexists_needs_review')
                    docmap[ver]=existing_doc['id']
                    if dst.execute('SELECT count(*) AS n FROM historical_unawarded_expert_vacancies WHERE document_id=%s',(existing_doc['id'],)).fetchone()['n']!=0:raise ValueError('existing_expert_rows_require_review')
                    dst.execute('INSERT INTO recovery_0519.document_map VALUES(%s,%s,%s,%s,%s,%s)',(sha,docmap[ver],ev[ver]['url'],ev[ver]['page_url'],ev[ver]['html_sha'],'html_link_observed_bytes_later'))
                    continue
                v=new.execute('SELECT * FROM document_versions WHERE id=%s',(src['document_version_id'],)).fetchone()
                vid=insert_row(dst,'document_versions',{c:v[c] for c in fieldmap['document_versions']})
                pr=new.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(src['document_version_id'],)).fetchone()
                if not pr or pr['rows_extracted']!=EXPECTED[ver]:raise ValueError('source_parse_run_mismatch')
                prdata={c:pr[c] for c in fieldmap['document_parse_runs']};prdata['document_version_id']=vid
                prid=insert_row(dst,'document_parse_runs',prdata)
                d=new.execute('SELECT * FROM documents WHERE id=%s',(src['id'],)).fetchone()
                data={c:d[c] for c in fieldmap['documents']};data.update(document_version_id=vid,source_id=source['id'],
                    validation_status='needs_validation',validation_parse_run_id=prid,historical_status='unknown',publication_date_quality='unknown')
                docmap[ver]=insert_row(dst,'documents',data)
                dst.execute('INSERT INTO recovery_0519.document_map VALUES(%s,%s,%s,%s,%s,%s)',
                    (sha,docmap[ver],ev[ver]['url'],ev[ver]['page_url'],ev[ver]['html_sha'],'html_link_observed_bytes_later'))
            for ver in (1000067,1000444):
                m={}
                with new.cursor(name='award_'+str(ver),row_factory=dict_row) as cur:
                    cur.execute('SELECT * FROM award_results WHERE document_id=%s ORDER BY id',(docs[ver]['id'],))
                    while batch:=cur.fetchmany(500):
                        for row in batch:
                            data={c:row[c] for c in fieldmap['award_results']};data['document_id']=docmap[ver]
                            ident=insert_row(dst,'award_results',data);m[row['id']]=ident
                            dst.execute('INSERT INTO recovery_0519.row_map VALUES(%s,%s,%s)',('award_results',row['id'],ident))
                maps.update(m);totals[str(ver)]=len(m)
            assigned=0
            with new.cursor(name='awards_19nov',row_factory=dict_row) as cur:
                cur.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                  WHERE r.document_id=ANY(%s) ORDER BY a.id''',([docs[v]['id'] for v in (1000067,1000444)],))
                while batch:=cur.fetchmany(500):
                    for row in batch:
                        if row.get('matched_offered_position_id') is not None:raise ValueError('unaccredited_association_source')
                        data={c:row[c] for c in fieldmap['award_assignments']};data['award_result_id']=maps[row['award_result_id']]
                        ident=insert_row(dst,'award_assignments',data)
                        dst.execute('INSERT INTO recovery_0519.row_map VALUES(%s,%s,%s)',('award_assignments',row['id'],ident));assigned+=1
            for ver in (1000005,1000535):
                label='provisional' if ver==1000005 else 'definitivo'
                with new.cursor(name='offered_'+str(ver),row_factory=dict_row) as cur:
                    cur.execute('SELECT * FROM offered_positions WHERE document_id=%s ORDER BY id',(docs[ver]['id'],))
                    while batch:=cur.fetchmany(500):
                        for row in batch:
                            srcid=row.pop('id');row.pop('document_id',None);row.pop('created_at',None)
                            safe_payload=json.loads(json.dumps(row,default=str))
                            dst.execute('INSERT INTO recovery_quarantine.positions_19nov VALUES(%s,%s,%s,%s,%s)',(VERSIONS[ver],srcid,docmap[ver],Jsonb(safe_payload),label))
            expertcols=[c for c in cols(new,'historical_unawarded_expert_vacancies') if c in set(cols(dst,'historical_unawarded_expert_vacancies'))]
            for row in new.execute('SELECT * FROM historical_unawarded_expert_vacancies WHERE document_id=%s',(docs[1000391]['id'],)):
                data={c:row[c] for c in expertcols};data['document_id']=docmap[1000391]
                if data['result_status']!='unawarded_no_requests':raise ValueError('expert_status_conflict')
                from psycopg import sql
                dst.execute(sql.SQL('INSERT INTO historical_unawarded_expert_vacancies ({}) VALUES ({})').format(
                    sql.SQL(',').join(map(sql.Identifier,data)),sql.SQL(',').join(sql.Placeholder()*len(data))),list(data.values()))
            for key,versions in (('expertos-2026-11-05',(1000391,)),('interino-2026-11-19',NOV19)):
                proc=new.execute('SELECT * FROM historical_procedures WHERE procedure_key=%s',(key,)).fetchone()
                if not proc or proc['publication_date'] is not None or proc['publication_date_quality']!='unknown':raise ValueError('procedure_date_conflict')
                dst.execute('INSERT INTO historical_procedures(procedure_key,procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality) VALUES(%s,%s,%s,%s,%s,%s)',
                    tuple(proc[k] for k in ('procedure_key','procedure_kind','procedure_date','date_evidence','publication_date','publication_date_quality')))
                for ver in versions:
                    link=new.execute('SELECT document_role,relationship_evidence FROM historical_procedure_documents WHERE procedure_key=%s AND document_id=%s',(key,docs[ver]['id'])).fetchone()
                    if not link:raise ValueError('source_procedure_link_missing')
                    dst.execute('INSERT INTO historical_procedure_documents VALUES(%s,%s,%s,%s)',(key,docmap[ver],link['document_role'],link['relationship_evidence']))
            after=counts(dst)
            if any(before[t]!=after[t] for t in ('offered_positions','centers','public_alerts','notification_posts','push_notification_events')):raise ValueError('unintended_data_change')
            if dst.execute('SELECT count(*) AS n FROM recovery_17_review.documents' if baseline17 else 'SELECT count(*) AS n FROM recovery_quarantine.positions').fetchone()['n']!=qbefore:raise ValueError('prior_quarantine_changed')
            if dst.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n']!=598:raise ValueError('quarantine_19nov_count_mismatch')
            links=0 if baseline17 else dst.execute('''SELECT count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                JOIN recovery_03nov.document_map m ON m.target_document_id=r.document_id WHERE a.matched_offered_position_id IS NOT NULL''').fetchone()['n']
            if not baseline17 and links!=213:raise ValueError('prior_associations_changed')
            try:
                with dst.transaction():
                    dst.execute("DELETE FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-19'")
                    raise RuntimeError('injected')
            except RuntimeError as exc:
                if str(exc)!='injected':raise
            if dst.execute("SELECT count(*) AS n FROM historical_procedure_documents WHERE procedure_key='interino-2026-11-19'").fetchone()['n']!=4:raise ValueError('rollback_failed')
    new.rollback()
    return {'status':'INTEGRATED_0519','before':before,'after':after,'documents':5,'award_results':totals,
      'award_assignments_unmatched':assigned,'expert_unawarded':6,'offer_versions_quarantined':2,'offer_rows_per_version':299,
      'prior_quarantined_rows':qbefore,'prior_strict_associations':None if baseline17 else 213,'backup_database':backup_db,
      'rollback_verified':True,'notification_counts_unchanged':True,'date_quality':'unknown',
      'limit':'URL labels observed in HTML, but downloaded bytes not proven identical at capture time.'}

def check_api(socket):
    import asyncio,os,httpx
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5,'dbname':'baseline_trial_20261123',
        'options':'-c default_transaction_read_only=on'}
    os.environ['RADAR_DOCENT_DB_URL']=psycopg.conninfo.make_conninfo(**kw);os.environ['ENABLE_NOTIFICATIONS']='0'
    from app.api.main import app
    from app.storage.db import close_pool,get_pool
    with get_pool().connection() as db:
        ident=db.execute("SELECT current_database() AS n,current_setting('data_directory') AS d,current_setting('transaction_read_only') AS r").fetchone()
        if ident!={'n':'baseline_trial_20261123','d':'/work/data','r':'on'}:raise ValueError('api_identity_mismatch')
    async def run_checks():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
            results={}
            listing=await client.get('/api/historical-procedures')
            if listing.status_code!=200 or not {'interino-2026-11-03','expertos-2026-11-05','interino-2026-11-19'}<={x['procedure_key'] for x in listing.json()['items']}:raise ValueError('procedure_listing_mismatch')
            expert=await client.get('/api/historical-procedures/expertos-2026-11-05')
            if expert.status_code!=200:raise ValueError('expert_endpoint_failed')
            ex=expert.json()
            if len(ex['documents'])!=1 or ex['documents'][0]['source_document_sha256']!=VERSIONS[1000391] or len(ex['unawarded_vacancies'])!=6:raise ValueError('expert_response_mismatch')
            if any(x['result_status']!='unawarded_no_requests' for x in ex['unawarded_vacancies']):raise ValueError('expert_status_mismatch')
            exoffers=await client.get('/api/offered-positions',params={'document_id':ex['documents'][0]['document_id'],'include_unavailable':'true'})
            if exoffers.status_code!=200 or exoffers.json()['total']!=0:raise ValueError('unawarded_vacancies_in_offers')
            nov=await client.get('/api/historical-procedures/interino-2026-11-19')
            if nov.status_code!=200:raise ValueError('nov19_endpoint_failed')
            body=nov.json();ds={x['source_document_sha256']:x for x in body['documents']}
            if set(ds)!={VERSIONS[v] for v in NOV19}:raise ValueError('nov19_documents_mismatch')
            verified=evidence()
            for ver in NOV19:
                if ds[VERSIONS[ver]].get('official_document_url')!=verified[ver]['url'] or ds[VERSIONS[ver]].get('document_url_evidence')!='html_link_observed_bytes_later':raise ValueError('nov19_source_url_mismatch')
            if ex['documents'][0].get('official_document_url')!=verified[1000391]['url']:raise ValueError('expert_source_url_mismatch')
            if ds[VERSIONS[1000067]]['extracted_rows']!=6028 or ds[VERSIONS[1000444]]['extracted_rows']!=10659:raise ValueError('nov19_award_count_mismatch')
            for ver,label in ((1000005,'provisional'),(1000535,'definitivo')):
                row=ds[VERSIONS[ver]]
                if row.get('quarantined_rows')!=299 or row.get('extracted_rows')!=299 or row.get('extraction_storage')!='quarantined' or row.get('document_stage_label')!=label or row.get('stage_evidence')!='url_label_observed_bytes_at_capture_unverified':raise ValueError('offer_quarantine_response_mismatch')
                offers=await client.get('/api/offered-positions',params={'document_id':row['document_id'],'include_unavailable':'true'})
                if offers.status_code!=200 or offers.json()['total']!=0:raise ValueError('quarantine_offers_visible')
            if body['procedure']['publication_date'] is not None or body['procedure']['publication_date_quality']!='unknown':raise ValueError('publication_date_conflict')
            if any(x.get('validation_status')!='needs_validation' or x.get('historical_status')!='unknown' for x in body['documents']):raise ValueError('document_status_conflict')
            return {'procedure_listing_http':200,'procedures_present':3,'expert_http':200,'expert_vacancies':6,'expert_offered':0,
              'nov19_http':200,'nov19_result_counts':[6028,10659],'offer_labels':['provisional','definitivo'],
              'quarantine_rows':[299,299],'quarantine_rows_visible_as_offers':0,'publication_date':'unknown',
              'validation':'needs_validation','historical_status':'unknown'}
    try:return asyncio.run(run_checks())
    finally:close_pool()

"""Apply an explicitly evidenced batch to the owned trial, never to production."""
import hashlib,json,os
from pathlib import Path
import psycopg
from psycopg.rows import dict_row,tuple_row
from app.services.historical_batch_reconcile import (classify_document,copy_bag,copy_difficult,copy_awards,ambiguous_bag_ids,
    create_verified_document,load_mixed_rows,plan_mixed_rows,apply_mixed_difficult,apply_mixed_bag)
from app.services.discovery.verified_evidence import verify_html_link
from scripts.integrate_03nov_trial import ROOT,counts

OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
MANIFEST=OUT/'lote_reconciliacion_01.json'
SNAPSHOT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/inventario_hetzner_snapshot_v2.json'

def verify_evidence(entries):
    raw=SNAPSHOT.read_bytes()
    if hashlib.sha256(raw).hexdigest()!='95263b363a91c6e6083ef8ae95cd3bebb8fd36773a53cad6a416785ddd2126c5':raise ValueError('inventory_identity_mismatch')
    snap={x['version_id']:x for x in json.loads(raw)['versions']}
    plan={x['version_id']:x for x in json.loads((OUT/'plan_integral_reanudable.json').read_text())['entries']}
    bag={x['version_id']:x for x in json.loads((OUT/'validacion_bolsas_grupal_56.json').read_text())['documents']}
    difficult={x['version_id']:x for x in json.loads((OUT/'validacion_dificil_grupal_56.json').read_text())['documents']}
    for entry in entries:
        v=entry['version_id'];sha=entry['sha256'];item=plan.get(v);meta=snap.get(v)
        if not item or item['sha256']!=sha or (meta is not None and meta['sha256']!=sha):raise ValueError('candidate_sha_not_accredited')
        pdf=Path(item['local_sha_verified_paths'][0])
        if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=sha:raise ValueError('candidate_pdf_mismatch')
        if meta:
            urls={a['canonical_url'] for a in meta['assets'] if a.get('canonical_url','').startswith('https://ceice.gva.es/documents/')}
        else:
            urls=set(item['provenance']['urls'])
            if entry['eligibility']!='quarantine' or len(urls)!=1:raise ValueError('new_source_requires_review')
            verified=False
            for group in item['provenance']['evidence']:
                for ev in group:
                    try:
                        p=ROOT/ev['path']
                        if p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()):continue
                        verify_html_link(html_bytes=p.read_bytes(),expected_html_sha256=ev['sha256'],final_page_url=ev['final_url'],linked_url=next(iter(urls)))
                        entry['html_sha']=ev['sha256'];entry['page_url']=ev['final_url'];verified=True
                    except (ValueError,OSError,KeyError):continue
            if not verified:raise ValueError('new_source_html_evidence_missing')
        if len(urls)!=1:raise ValueError('official_document_url_ambiguous')
        entry['verified_url']=urls.pop()
        assets=[a for a in (meta['assets'] if meta else []) if a.get('source_key')=='rrhh_main' and a.get('source_url')=='https://ceice.gva.es/es/web/rrhh-educacion' and a.get('canonical_url')==entry['verified_url']]
        entry['official_asset']=assets[0] if len(assets)==1 else None
        if entry['eligibility']=='apply_with_limitations':
            if entry['family']=='award_results':
                prior=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/consolidacion_validacion_17.json').read_text())
                prior=next((r for r in prior['records'] if r.get('version_id')==v),None)
                current=json.loads((OUT/'validacion_procedimientos_final_20261122.json').read_text())
                current=next((r for r in current['documents'] if r.get('sha256')==sha),None)
                if not prior or prior.get('sha256')!=sha or prior.get('technical_status')!='validated_extraction' or not current or not current.get('parse_run'):raise ValueError('award_independent_validation_missing')
                report={'sha256':sha,'status':'validated_with_limitations','parser_key':current['parse_run']['parser_key'],
                        'parser_version':current['parse_run']['parser_version'],'stored_rows':prior['records']}
            else:report=(bag if entry['family']=='bag_members' else difficult).get(v)
            if not report or report.get('sha256')!=sha or report.get('status')!='validated_with_limitations':raise ValueError('independent_validation_missing')
            if entry['family']=='bag_members' and not report.get('identity_multiset_matches'):raise ValueError('bag_identity_not_checked')
            if entry['family']=='difficult_candidates' and (report.get('source_only')!=0 or report.get('stored_only')!=0 or report.get('ordered_differences')!=0):raise ValueError('candidate_census_mismatch')
            entry['validation']=report

def run(new,socket,manifest_path=MANIFEST,*,target_db='baseline_trial_20261123',baseline17=False):
    if baseline17 and target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh'):raise ValueError('baseline17_trial_identity_required')
    if not baseline17 and target_db!='baseline_trial_20261123':raise ValueError('legacy_trial_identity_required')
    manifest_bytes=manifest_path.read_bytes();manifest=json.loads(manifest_bytes)
    if manifest.get('format')!='historical-reconciliation-batch-v1':raise ValueError('manifest_format_invalid')
    entries=manifest['documents'];verify_evidence(entries)
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        backup=({'baseline17_fresh':'baseline17_fresh_prebatch','baseline17_reproduction':'baseline17_reproduction_prebatch'}.get(target_db,'baseline17_trial_prebatch')) if baseline17 else 'baseline_trial_prebatch_20261123'
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(backup,)).fetchone():
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(backup),sql.Identifier(target_db)))
    new.row_factory=dict_row;new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
        ident=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
        if ident!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('trial_identity_mismatch')
        before=counts(dst)
        for table in ('docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates'):
            before[table]=dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']
        qbefore=[] if baseline17 else [dst.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('recovery_quarantine.positions','recovery_quarantine.positions_19nov')]
        if baseline17:
            qbefore=[dst.execute('SELECT count(*) AS n FROM recovery_17_review.documents').fetchone()['n']]
        if not baseline17 and qbefore!=[58,598]:raise ValueError('quarantine_changed')
        dst.commit()
        with dst.transaction():
            dst.execute('''CREATE SCHEMA IF NOT EXISTS recovery_batch;
              CREATE TABLE IF NOT EXISTS recovery_batch.documents(source_sha text PRIMARY KEY,target_document_id bigint NOT NULL REFERENCES documents(id),family text NOT NULL,
                source_parser text NOT NULL,source_parser_version text NOT NULL,source_rows integer NOT NULL,quality text NOT NULL,official_url text NOT NULL,manifest_sha text NOT NULL);
              CREATE TABLE IF NOT EXISTS recovery_batch.row_map(kind text NOT NULL,source_id bigint NOT NULL,target_id bigint NOT NULL,PRIMARY KEY(kind,source_id),UNIQUE(kind,target_id));
              CREATE TABLE IF NOT EXISTS recovery_batch.quarantined_rows(kind text NOT NULL,source_id bigint NOT NULL,source_sha text NOT NULL,reason text NOT NULL,PRIMARY KEY(kind,source_id));
              CREATE TABLE IF NOT EXISTS recovery_batch.quarantined_documents(source_sha text PRIMARY KEY,source_version_id bigint NOT NULL,official_url text NOT NULL,page_url text,html_sha text,reason text NOT NULL);
              CREATE TABLE IF NOT EXISTS recovery_batch.mixed_changes(source_sha text NOT NULL,kind text NOT NULL,source_id bigint NOT NULL,target_id bigint NOT NULL,justification text NOT NULL,PRIMARY KEY(kind,source_id));
              ALTER TABLE recovery_batch.documents ADD COLUMN IF NOT EXISTS source_total integer;''')
            dst.execute('UPDATE recovery_batch.documents SET source_total=source_rows WHERE source_total IS NULL')
            results=[];secret=os.urandom(32)
            for entry in entries:
                sha=entry['sha256'];ver=entry['version_id'];family=entry['family']
                src=new.execute('''SELECT d.id,d.document_version_id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchone()
                target=dst.execute('''SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchone()
                row={'version_id':ver,'sha256':sha,'family':family,'eligibility':entry['eligibility']}
                if not src:row.update(classification='E',outcome='quarantine',reason='source_document_missing');results.append(row);continue
                if not target and entry['eligibility']!='apply_with_limitations':
                    dst.execute('''INSERT INTO recovery_batch.quarantined_documents VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT(source_sha) DO NOTHING''',
                      (sha,ver,entry['verified_url'],entry.get('page_url'),entry.get('html_sha'),entry['reason']))
                    row.update(classification='D',outcome='quarantine',reason='new_document_not_authorized_for_import');results.append(row);continue
                prior=dst.execute('SELECT source_rows,source_total FROM recovery_batch.documents WHERE source_sha=%s',(sha,)).fetchone()
                if prior:
                    if not target:raise ValueError('existing_batch_target_missing')
                    removed=0
                    if family=='bag_members':
                        for source_id in ambiguous_bag_ids(new,src['id']):
                            mapped=dst.execute("SELECT target_id FROM recovery_batch.row_map WHERE kind='docent_bag_members' AND source_id=%s",(source_id,)).fetchone()
                            if mapped:
                                deleted=dst.execute('''DELETE FROM docent_bag_members m USING docent_bag_snapshots s
                                    WHERE m.id=%s AND m.snapshot_id=s.id AND s.document_id=%s''',(mapped['target_id'],target['id'])).rowcount
                                if deleted!=1:raise ValueError('ambiguous_row_identity_mismatch')
                                dst.execute("DELETE FROM recovery_batch.row_map WHERE kind='docent_bag_members' AND source_id=%s",(source_id,));removed+=1
                            dst.execute("INSERT INTO recovery_batch.quarantined_rows(kind,source_id,source_sha,reason) VALUES('docent_bag_members',%s,%s,'ambiguous_documentary_key') ON CONFLICT DO NOTHING",(source_id,sha))
                        if removed:
                            dst.execute('UPDATE recovery_batch.documents SET source_rows=source_rows-%s WHERE source_sha=%s',(removed,sha))
                            prior['source_rows']-=removed
                    if family=='bag_members':
                        n=dst.execute('SELECT count(*) AS n FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s',(target['id'],)).fetchone()['n']
                    elif family=='difficult_candidates':
                        n=dst.execute('SELECT count(*) AS n FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s',(target['id'],)).fetchone()['n']
                    elif family=='award_results':
                        n=dst.execute('SELECT count(*) AS n FROM award_results WHERE document_id=%s',(target['id'],)).fetchone()['n']
                    else:raise ValueError('replay_adapter_unknown')
                    if n!=prior['source_rows']:raise ValueError('replay_row_count_mismatch')
                    partial=prior['source_total'] is not None and prior['source_rows']<prior['source_total']
                    row.update(classification='E' if partial else 'A',outcome='partially_integrated' if partial else 'already_integrated',source_rows=prior['source_rows'],
                        pending_source_rows=(prior['source_total']-prior['source_rows'] if prior['source_total'] is not None else 0),ambiguous_previously_added_rows_removed=removed)
                    results.append(row);continue
                if entry['eligibility']=='quarantine':row.update(classification='E',outcome='quarantine',reason=entry['reason']);results.append(row);continue
                # Comparator operates on document-local keys, not names.
                if target:
                    new.row_factory=tuple_row;dst.row_factory=tuple_row
                    category,data=classify_document(dst,new,sha,family,secret)
                    new.row_factory=dict_row;dst.row_factory=dict_row
                    row.update(classification=category,comparison={k:v for k,v in data.items() if k!='differences'})
                else:category='D';row.update(classification='D')
                mixed=None
                if target and category in ('C','E') and family in ('bag_members','difficult_candidates'):
                    oldrows,keys,fields=load_mixed_rows(dst,target['id'],family)
                    newrows,_,_=load_mixed_rows(new,src['id'],family)
                    mixed=plan_mixed_rows(oldrows,newrows,keys,fields)
                    from collections import Counter
                    row['mixed_plan']={'actions':dict(Counter(a['category'] for a in mixed['actions'])),
                      'unkeyed_baseline':mixed['unkeyed_baseline'],'baseline_only_keys':mixed['baseline_only_keys']}
                mixed_allowed=bool(mixed and family in ('difficult_candidates','bag_members') and entry['eligibility']=='apply_with_limitations')
                if entry['eligibility']!='apply_with_limitations' or (category not in ('B','D') and not mixed_allowed):
                    row.update(outcome='preserved' if category=='A' else 'quarantine',reason='no_automatic_changes_to_existing_or_ambiguous_rows');results.append(row);continue
                report=entry['validation'];parser=new.execute('''SELECT * FROM document_parse_runs
                    WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1''',(src['document_version_id'],)).fetchone()
                expected=report.get('stored_rows')
                if not parser or parser['parser_key']!=report['parser_key'] or parser['parser_version']!=report['parser_version'] or parser['rows_extracted']!=expected:
                    row.update(outcome='quarantine',reason='parser_evidence_mismatch');results.append(row);continue
                if mixed_allowed:
                    added=(apply_mixed_difficult(new,dst,mixed,target['id'],sha) if family=='difficult_candidates'
                           else apply_mixed_bag(new,dst,mixed,target['id'],src['id'],sha))
                    row.update(outcome='mixed_processed',added=added,validation='validated_with_limitations',publication_date='unknown',vigency='unknown')
                    results.append(row);continue
                try:
                    with dst.transaction():
                        if category=='D':target={'id':create_verified_document(new,dst,src,parser,entry['official_asset'])}
                        added=(copy_bag(new,dst,src['id'],target['id'],entry['verified_url'],sha) if family=='bag_members'
                            else copy_difficult(new,dst,src['id'],target['id']) if family=='difficult_candidates'
                            else copy_awards(new,dst,src['id'],target['id'],sha))
                        total=added['bag_members'] if family=='bag_members' else added['difficult_candidates'] if family=='difficult_candidates' else added['award_results']
                        pending=added.get('ambiguous_rows',0)
                        if total+pending!=expected or not total:raise ValueError('validated_count_mismatch')
                        quality='partial_validated_with_limitations' if pending else 'validated_with_limitations'
                        dst.execute('''INSERT INTO recovery_batch.documents(source_sha,target_document_id,family,source_parser,source_parser_version,source_rows,quality,official_url,manifest_sha,source_total)
                            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                            (sha,target['id'],family,parser['parser_key'],parser['parser_version'],total,quality,entry['verified_url'],hashlib.sha256(manifest_bytes).hexdigest(),expected))
                except ValueError as exc:
                    row.update(outcome='quarantine',reason=str(exc));results.append(row);continue
                row.update(outcome='inserted',added=added,validation=quality,publication_date='unknown',vigency='unknown');results.append(row)
            after=counts(dst)
            for table in ('docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates'):
                after[table]=dst.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']
            created=sum(x.get('classification')=='D' and x.get('outcome')=='inserted' for x in results)
            if after['documents']!=before['documents']+created or after['document_versions']!=before['document_versions']+created:raise ValueError('document_count_mismatch')
            expected_results=sum(x.get('added',{}).get('award_results',0) for x in results)
            expected_assignments=sum(x.get('added',{}).get('award_assignments',0) for x in results)
            if after['award_results']!=before['award_results']+expected_results or after['award_assignments']!=before['award_assignments']+expected_assignments:raise ValueError('award_count_mismatch')
            if any(before[t]!=after[t] for t in ('offered_positions','centers','public_alerts','notification_posts','push_notification_events')):raise ValueError('unrelated_data_changed')
            if not baseline17 and [dst.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('recovery_quarantine.positions','recovery_quarantine.positions_19nov')]!=qbefore:raise ValueError('quarantine_changed')
            if baseline17 and dst.execute('SELECT count(*) AS n FROM recovery_17_review.documents').fetchone()['n']!=qbefore[0]:raise ValueError('quarantine_changed')
            if dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()['n']:raise ValueError('foreign_keys_unvalidated')
            marker_before=dst.execute('SELECT count(*) AS n FROM recovery_batch.documents').fetchone()['n']
            try:
                with dst.transaction():
                    dst.execute("DELETE FROM recovery_batch.documents WHERE source_sha=%s",(entries[0]['sha256'],))
                    raise RuntimeError('injected_rollback')
            except RuntimeError as exc:
                if str(exc)!='injected_rollback':raise
            if dst.execute('SELECT count(*) AS n FROM recovery_batch.documents').fetchone()['n']!=marker_before:raise ValueError('rollback_marker_missing')
        new.rollback()
    return {'status':'BATCH_RECONCILED','manifest_sha256':hashlib.sha256(manifest_bytes).hexdigest(),'documents':results,
            'before':before,'after':after,'quarantines':qbefore,'rollback_tested':True,'external_notifications':'unchanged',
            'new_documents':'require verified rrhh_main inventory metadata and an allowed family adapter',
            'existing_mixed_differences':'preserved and quarantined; no field overwritten'}

def check_api(new,socket):
    import asyncio,httpx
    from psycopg.conninfo import make_conninfo
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5,'dbname':'baseline_trial_20261123',
        'options':'-c default_transaction_read_only=on'}
    with psycopg.connect(**kw,row_factory=dict_row) as dst:
        markers={r['family']:r['target_document_id'] for r in dst.execute('SELECT family,target_document_id FROM recovery_batch.documents')}
        samplepos=dst.execute("SELECT source_id,target_id FROM recovery_batch.row_map WHERE kind='difficult_coverage_positions' ORDER BY source_id LIMIT 1").fetchone()
    new.row_factory=dict_row
    samplebag=new.execute('''SELECT m.person_name_normalized,m.order_number FROM docent_bag_members m
      JOIN docent_bag_snapshots s ON s.id=m.snapshot_id JOIN documents d ON d.id=s.document_id
      JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s
      AND NOT EXISTS(SELECT 1 FROM docent_bag_members other JOIN docent_bag_snapshots os ON os.id=other.snapshot_id
        WHERE os.document_id=s.document_id AND os.list_scope=s.list_scope AND os.position_scope=s.position_scope
        AND os.staff_kind=s.staff_kind AND os.specialty_code=s.specialty_code AND other.order_number=m.order_number AND other.id<>m.id)
      LIMIT 1''',('acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0',)).fetchone()
    sampleaward=new.execute('''SELECT r.person_name_normalized,r.order_number FROM award_results r JOIN documents d ON d.id=r.document_id
      JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s AND r.specialty_code IS NOT NULL AND r.order_number IS NOT NULL
      AND NOT EXISTS(SELECT 1 FROM award_results x WHERE x.document_id=r.document_id AND x.list_scope=r.list_scope
        AND x.body_code IS NOT DISTINCT FROM r.body_code AND x.specialty_code=r.specialty_code AND x.order_number=r.order_number AND x.id<>r.id)
      LIMIT 1''',('2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4',)).fetchone()
    expected_candidates=(new.execute('SELECT count(*) AS n FROM difficult_coverage_candidates WHERE position_id=%s',(samplepos['source_id'],)).fetchone()['n'] if samplepos else None)
    new.rollback();os.environ['RADAR_DOCENT_DB_URL']=make_conninfo(**kw);os.environ['ENABLE_NOTIFICATIONS']='0'
    from app.api.main import app
    from app.storage.db import close_pool,get_pool
    with get_pool().connection() as db:
        ident=db.execute("SELECT current_database() AS n,current_setting('transaction_read_only') AS ro").fetchone()
        if ident!={'n':'baseline_trial_20261123','ro':'on'}:raise ValueError('api_target_mismatch')
    async def checks():
        result={}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://testserver') as client:
            if 'bag_members' in markers:
                if not samplebag:raise ValueError('bag_source_sample_missing')
                r=await client.get('/api/persons/profile',params={'normalized_name':samplebag['person_name_normalized']})
                if r.status_code!=200:raise ValueError('bag_profile_api_failure')
                matches=[x for x in r.json().get('bag_memberships',[]) if x.get('document_id')==markers['bag_members'] and x.get('order_number')==samplebag['order_number']]
                if not matches or any(x.get('staff_kind')!='career_practice' for x in matches):raise ValueError('bag_profile_content_mismatch')
                if any(x.get('extraction_completeness')!='partial' or x.get('pending_source_rows')!=16 for x in matches):raise ValueError('partial_extraction_api_missing')
                result['career_practice_profile']={'http_status':200,'matching_source_rows':len(matches),'staff_kind':'career_practice','partial_extraction_visible':True,'pending_source_rows':16}
            if 'difficult_candidates' in markers:
                r=await client.get('/api/difficult-coverage/positions',params={'document_id':markers['difficult_candidates'],'include_unavailable':'true','limit':100})
                if r.status_code!=200 or r.json().get('total')!=52:raise ValueError('difficult_positions_api_mismatch')
                items=r.json().get('items',[])
                if not items or any(x.get('availability_status')!='unknown' or x.get('reconciliation_quality')!='validated_with_limitations'
                    or x.get('extraction_completeness')!='complete_with_limitations' for x in items):raise ValueError('difficult_quality_api_mismatch')
                default=await client.get('/api/difficult-coverage/positions',params={'document_id':markers['difficult_candidates']})
                if default.status_code!=200 or default.json().get('total')!=0:raise ValueError('unknown_vigency_exposed_as_current')
                c=await client.get('/api/difficult-coverage/positions/'+str(samplepos['target_id'])+'/candidates',params={'limit':1})
                if c.status_code!=200 or c.json().get('total')!=expected_candidates:raise ValueError('candidate_api_content_mismatch')
                result['difficult_coverage']={'http_status':200,'positions':52,'default_current_positions':0,
                    'sample_candidates_match_source':True,'sample_candidate_count':expected_candidates}
            if 'award_results' in markers:
                if not sampleaward:raise ValueError('award_sample_missing')
                r=await client.get('/api/persons/profile',params={'normalized_name':sampleaward['person_name_normalized']})
                if r.status_code!=200:raise ValueError('award_profile_api_failure')
                matches=[x for x in r.json().get('awards',[]) if x.get('document_id')==markers['award_results'] and x.get('order_number')==sampleaward['order_number']]
                if not matches or any(x.get('extraction_completeness')!='partial' or x.get('pending_source_rows')!=8 for x in matches):raise ValueError('award_profile_content_mismatch')
                result['career_award_profile']={'http_status':200,'matching_source_rows':len(matches),'partial_extraction_visible':True,'pending_source_rows':8}
        return result
    try:return asyncio.run(checks())
    finally:close_pool()

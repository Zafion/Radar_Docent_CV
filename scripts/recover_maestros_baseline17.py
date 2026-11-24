"""Reconcile the validated 03-Nov Maestros extraction by unique document ordinal."""
from collections import Counter
import hashlib,json
import psycopg
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import ROOT,SHAS,cols,counts,insert_row

def plan_ordinals(old,new):
    a=Counter(r['order_number'] for r in old);b=Counter(r['order_number'] for r in new)
    if None in a or None in b or any(v!=1 for v in a.values()) or any(v!=1 for v in b.values()):raise ValueError('document_ordinal_not_unique')
    if a.keys()-b.keys():raise ValueError('existing_ordinal_not_in_validated_extraction')
    return set(b)-set(a)

def plan_exact_blocks(old,new):
    a=Counter(hashlib.sha256(r['raw_block_text'].encode()).hexdigest() for r in old)
    b=Counter(hashlib.sha256(r['raw_block_text'].encode()).hexdigest() for r in new)
    if a-b:raise ValueError('existing_blocks_not_in_validated_extraction')
    extra=b-a
    if any(v!=1 or b[k]!=1 for k,v in extra.items()):raise ValueError('missing_blocks_not_unique')
    return set(extra)

def recover_secondary(source,socket,*,target_db='baseline17_trial'):
    if target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh'):raise ValueError('target_identity')
    plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
    item=next(x for x in plan['entries'] if x['version_id']==701)
    pdf=__import__('pathlib').Path(item['local_sha_verified_paths'][0])
    if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=SHAS[701]:raise ValueError('secondary_pdf_sha_mismatch')
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    source.row_factory=dict_row;source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        doc=source.execute('SELECT d.id,d.document_version_id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHAS[701],)).fetchone()
        parse=source.execute("SELECT rows_extracted FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(doc['document_version_id'],)).fetchone()
        if not parse or parse['rows_extracted']!=11024:raise ValueError('secondary_parse_validation_missing')
        rows=source.execute('SELECT * FROM award_results WHERE document_id=%s',(doc['id'],)).fetchall()
        if len(rows)!=11024:raise ValueError('secondary_source_count')
        with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
            identity=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
            if identity!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
            before=counts(dst);dst.commit()
            with dst.transaction():
                target=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHAS[701],)).fetchone()
                old=dst.execute('SELECT raw_block_text FROM award_results WHERE document_id=%s',(target['id'],)).fetchall()
                if len(old) not in (11022,11024):raise ValueError('secondary_target_count')
                missing=plan_exact_blocks(old,rows)
                if len(missing) not in (0,2):raise ValueError('secondary_unexpected_missing_count')
                fields=[c for c in cols(source,'award_results') if c in set(cols(dst,'award_results')) and c!='id']
                dst.execute('CREATE TABLE IF NOT EXISTS recovery_03nov.secondary_rows(source_block_sha text PRIMARY KEY,target_result_id bigint NOT NULL UNIQUE REFERENCES award_results(id))')
                for row in rows:
                    sha=hashlib.sha256(row['raw_block_text'].encode()).hexdigest()
                    if sha not in missing:continue
                    if row['status']!='No adjudicat' or source.execute('SELECT count(*) AS n FROM award_assignments WHERE award_result_id=%s',(row['id'],)).fetchone()['n']!=0:raise ValueError('secondary_missing_result_requires_review')
                    data={c:row[c] for c in fields};data['document_id']=target['id'];tid=insert_row(dst,'award_results',data)
                    dst.execute('INSERT INTO recovery_03nov.secondary_rows VALUES(%s,%s)',(sha,tid))
                after=counts(dst)
                if after['award_results']!=before['award_results']+len(missing) or any(after[t]!=before[t] for t in before if t!='award_results'):raise ValueError('secondary_unrelated_data_changed')
            return {'status':'SECONDARY_RECONCILED','source_rows':11024,'inserted_results':len(missing),'before':before,'after':after,'identity':'unique_full_source_block','assignments_created':0}
    finally:source.rollback()

def run(source,socket,*,target_db='baseline17_trial'):
    if target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh'):raise ValueError('target_identity')
    plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
    item=next(x for x in plan['entries'] if x['version_id']==700)
    pdf=__import__('pathlib').Path(item['local_sha_verified_paths'][0])
    if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=SHAS[700]:raise ValueError('pdf_sha_mismatch')
    from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
    parser=FinalAwardListingMaestrosParserService()
    parsed=parser._parse_document({'file_path':str(pdf)})
    if len(parsed)!=6416:raise ValueError('pdf_parser_count_mismatch')
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        backup={'baseline17_fresh':'baseline17_fresh_pre_maestros','baseline17_reproduction':'baseline17_reproduction_pre_maestros'}.get(target_db,'baseline17_pre_maestros')
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(backup,)).fetchone():
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(backup),sql.Identifier(target_db)))
    source.row_factory=dict_row;source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        source_doc=source.execute('SELECT d.id,d.document_version_id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHAS[700],)).fetchone()
        if not source_doc:raise ValueError('source_missing')
        parse=source.execute("SELECT rows_extracted FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(source_doc['document_version_id'],)).fetchone()
        if not parse or parse['rows_extracted']!=6416:raise ValueError('source_parse_validation_missing')
        rows=source.execute('SELECT * FROM award_results WHERE document_id=%s ORDER BY order_number',(source_doc['id'],)).fetchall()
        if len(rows)!=6416:raise ValueError('source_row_count_mismatch')
        if Counter((r.order_number,r.status) for r in parsed)!=Counter((r['order_number'],r['status']) for r in rows):raise ValueError('pdf_source_identity_or_status_mismatch')
        with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
            ident=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
            if ident!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
            before=counts(dst);dst.commit()
            with dst.transaction():
                dst.execute('''CREATE TABLE IF NOT EXISTS recovery_03nov.maestros_rows(source_ordinal integer PRIMARY KEY,target_result_id bigint NOT NULL UNIQUE REFERENCES award_results(id),raw_identical boolean NOT NULL);
                   CREATE TABLE IF NOT EXISTS recovery_03nov.maestros_pending(source_ordinal integer NOT NULL,reason text NOT NULL,PRIMARY KEY(source_ordinal,reason));''')
                target=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHAS[700],)).fetchone()
                offer_doc=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(SHAS[702],)).fetchone()
                if not target or not offer_doc:raise ValueError('target_document_missing')
                existing=dst.execute('SELECT id,order_number,raw_block_text FROM award_results WHERE document_id=%s',(target['id'],)).fetchall()
                missing=plan_ordinals(existing,rows);old={r['order_number']:r for r in existing}
                fieldmap={t:[c for c in cols(source,t) if c in set(cols(dst,t)) and c!='id'] for t in ('award_results','award_assignments')}
                source_ids={};source_results={r['id']:r for r in rows};inserted=0;ambiguous=0;reused=0;linked=0;assignments=0
                for row in rows:
                    ordinal=row['order_number']
                    if ordinal in missing:
                        data={c:row[c] for c in fieldmap['award_results']};data['document_id']=target['id']
                        tid=insert_row(dst,'award_results',data);inserted+=1;identical=True
                    else:
                        tid=old[ordinal]['id'];identical=old[ordinal]['raw_block_text']==row['raw_block_text'];reused+=1
                        if not identical:
                            dst.execute("INSERT INTO recovery_03nov.maestros_pending VALUES(%s,'raw_block_differs') ON CONFLICT DO NOTHING",(ordinal,));ambiguous+=1
                    source_ids[row['id']]=(tid,ordinal,identical)
                    dst.execute('INSERT INTO recovery_03nov.maestros_rows VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(ordinal,tid,identical))
                    mapping=dst.execute('SELECT target_result_id FROM recovery_03nov.maestros_rows WHERE source_ordinal=%s',(ordinal,)).fetchone()
                    if mapping['target_result_id']!=tid:raise ValueError('replay_mapping_conflict')
                for a in source.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s ORDER BY a.id''',(source_doc['id'],)):
                    tid,ordinal,identical=source_ids[a['award_result_id']]
                    prior=dst.execute('SELECT * FROM award_assignments WHERE award_result_id=%s',(tid,)).fetchall()
                    if prior:
                        if not any((p['position_code'],p['center_code'],p['petition_number'])==(a['position_code'],a['center_code'],a['petition_number']) for p in prior):
                            dst.execute("INSERT INTO recovery_03nov.maestros_pending VALUES(%s,'assignment_conflict') ON CONFLICT DO NOTHING",(ordinal,))
                        continue
                    if not identical:
                        dst.execute("INSERT INTO recovery_03nov.maestros_pending VALUES(%s,'assignment_identity_unverified') ON CONFLICT DO NOTHING",(ordinal,));continue
                    data={c:a[c] for c in fieldmap['award_assignments']};data['award_result_id']=tid;data['matched_offered_position_id']=None
                    if a['matched_offered_position_id'] is not None:
                        sp=source.execute('SELECT * FROM offered_positions WHERE id=%s',(a['matched_offered_position_id'],)).fetchone()
                        if not sp:raise ValueError('source_offer_missing')
                        specialty=a['position_specialty_code'] or source_results[a['award_result_id']]['specialty_code']
                        if (a['position_code'],a['center_code'],specialty)!=(sp['position_code'],sp['center_code'],sp['specialty_code']):raise ValueError('source_assignment_offer_conflict')
                        candidates=dst.execute('''SELECT id FROM offered_positions WHERE document_id=%s AND position_code=%s AND center_code=%s AND specialty_code=%s
                          AND (body_code IS NULL OR %s::text IS NULL OR body_code=%s)''',(offer_doc['id'],sp['position_code'],sp['center_code'],sp['specialty_code'],sp['body_code'],sp['body_code'])).fetchall()
                        if len(candidates)==1:
                            data['matched_offered_position_id']=candidates[0]['id'];linked+=1
                        else:dst.execute("INSERT INTO recovery_03nov.maestros_pending VALUES(%s,'offer_match_ambiguous') ON CONFLICT DO NOTHING",(ordinal,))
                    insert_row(dst,'award_assignments',data);assignments+=1
                after=counts(dst)
                if after['award_results']!=before['award_results']+inserted or after['award_assignments']!=before['award_assignments']+assignments:raise ValueError('row_counts_mismatch')
                if any(after[t]!=before[t] for t in ('documents','document_versions','offered_positions','centers','public_alerts','notification_posts','push_notification_events')):raise ValueError('unrelated_counts_changed')
                if dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()['n']:raise ValueError('unvalidated_fk')
                try:
                    with dst.transaction():
                        dst.execute('DELETE FROM recovery_03nov.maestros_rows')
                        raise RuntimeError('rollback_probe')
                except RuntimeError as e:
                    if str(e)!='rollback_probe':raise
                if dst.execute('SELECT count(*) AS n FROM recovery_03nov.maestros_rows').fetchone()['n']!=6416:raise ValueError('rollback_failed')
            return {'status':'MAESTROS_RECONCILED','pdf_sha256':SHAS[700],'source_rows':6416,'before':before,'after':after,'inserted_results':inserted,'reused_results':reused,'different_raw_blocks':ambiguous,'inserted_assignments':assignments,'new_strict_links':linked,'rollback_tested':True}
    finally:source.rollback()

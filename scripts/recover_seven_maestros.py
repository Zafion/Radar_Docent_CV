"""Reconcile only independently checked, unique-ordinal Maestros documents."""
from collections import Counter
import hashlib,json
from pathlib import Path
import psycopg
from psycopg.rows import dict_row
from scripts.integrate_03nov_trial import ROOT,cols,counts,insert_row
from scripts.recover_maestros_baseline17 import plan_ordinals

OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
VERSIONS=(436,79,138,62,243)

def shared_ordinal_conflicts(old,new):
    plan_ordinals(old,new)
    left={r['order_number']:r for r in old};right={r['order_number']:r for r in new}
    return sum(left[k]['person_name_normalized']!=right[k]['person_name_normalized'] for k in left.keys()&right.keys())

def run(source,socket,*,target_db='baseline17_trial'):
    if target_db not in ('baseline17_trial','baseline17_reproduction','baseline17_fresh'):raise ValueError('target_identity')
    proof={x['version']:x for x in json.loads((OUT/'revision_siete_maestros_20261124.json').read_text())['documents']}
    plan={x['version_id']:x for x in json.loads((OUT/'plan_integral_reanudable.json').read_text())['entries']}
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
        backup={'baseline17_fresh':'baseline17_fresh_pre_seven','baseline17_reproduction':'baseline17_reproduction_pre_seven'}.get(target_db,'baseline17_pre_seven')
        if not admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(backup,)).fetchone():
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(backup),sql.Identifier(target_db)))
    source.row_factory=dict_row;source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        results=[]
        for ver in VERSIONS:
            item=plan[ver];check=proof[ver];sha=item['sha256'];pdf=Path(item['local_sha_verified_paths'][0])
            if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=sha or not check['pdf_verified'] or not check.get('parser_matches_reconstruction') or check['sha256']!=sha:raise ValueError('source_evidence_mismatch')
            sd=source.execute('SELECT d.id,d.document_version_id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
            if not sd:raise ValueError('source_document_missing')
            rows=source.execute('SELECT * FROM award_results WHERE document_id=%s ORDER BY order_number',(sd['id'],)).fetchall()
            if len(rows)!=check['parser_rows'] or dict(Counter(r['status'] for r in rows))!=check['parser_statuses']:raise ValueError('source_parser_count_or_status_changed')
            with psycopg.connect(**kw,dbname='baseline',row_factory=dict_row,options='-c default_transaction_read_only=on') as original:
                baseline_rows=original.execute('''SELECT r.order_number,r.person_name_normalized FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(sha,)).fetchall()
            conflicts=shared_ordinal_conflicts(baseline_rows,rows)
            if conflicts:
                old_ordinals={r['order_number'] for r in baseline_rows}
                pending_ordinals={r['order_number'] for r in rows}-old_ordinals
                with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
                    identity=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
                    if identity!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
                    dst.commit()
                    with dst.transaction():
                        dst.execute('''CREATE TABLE IF NOT EXISTS recovery_03nov.other_maestros_pending(source_sha text NOT NULL,source_ordinal integer NOT NULL,reason text NOT NULL,PRIMARY KEY(source_sha,source_ordinal,reason))''')
                        td=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
                        if not td:raise ValueError('target_document_missing')
                        for ordinal in pending_ordinals:
                            if dst.execute('SELECT 1 FROM award_results WHERE document_id=%s AND order_number=%s',(td['id'],ordinal)).fetchone():raise ValueError('ambiguous_ordinal_already_in_target')
                            dst.execute("INSERT INTO recovery_03nov.other_maestros_pending VALUES(%s,%s,'shared_ordinal_person_conflict') ON CONFLICT DO NOTHING",(sha,ordinal))
                results.append({'version':ver,'sha256':sha,'source_rows':len(rows),'baseline_rows':len(baseline_rows),'inserted_results':0,'inserted_assignments':0,
                                'outcome':'quarantine','reason':'shared_ordinal_person_conflict','shared_ordinal_conflicts':conflicts,'pending_ordinals':len(pending_ordinals)})
                continue
            with psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
                ident=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
                if ident!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
                before=counts(dst);dst.commit()
                with dst.transaction():
                    dst.execute('''CREATE TABLE IF NOT EXISTS recovery_03nov.other_maestros_rows(source_sha text NOT NULL,source_ordinal integer NOT NULL,target_result_id bigint NOT NULL UNIQUE REFERENCES award_results(id),raw_identical boolean NOT NULL,PRIMARY KEY(source_sha,source_ordinal));
                      CREATE TABLE IF NOT EXISTS recovery_03nov.other_maestros_pending(source_sha text NOT NULL,source_ordinal integer NOT NULL,reason text NOT NULL,PRIMARY KEY(source_sha,source_ordinal,reason))''')
                    td=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
                    if not td:raise ValueError('target_document_missing')
                    old=dst.execute('SELECT id,order_number,raw_block_text FROM award_results WHERE document_id=%s',(td['id'],)).fetchall()
                    missing=plan_ordinals(old,rows);existing={r['order_number']:r for r in old}
                    fields={t:[c for c in cols(source,t) if c in set(cols(dst,t)) and c!='id'] for t in ('award_results','award_assignments')}
                    ids={};inserted=0;different=0;assignments=0;pending_links=0
                    for row in rows:
                        ordinal=row['order_number']
                        if ordinal in missing:
                            data={c:row[c] for c in fields['award_results']};data['document_id']=td['id'];tid=insert_row(dst,'award_results',data);inserted+=1;added=True;identical=True
                        else:
                            tid=existing[ordinal]['id'];identical=existing[ordinal]['raw_block_text']==row['raw_block_text'];added=False
                            if not identical:
                                dst.execute("INSERT INTO recovery_03nov.other_maestros_pending VALUES(%s,%s,'raw_block_differs') ON CONFLICT DO NOTHING",(sha,ordinal));different+=1
                        ids[row['id']]=(tid,ordinal,added)
                        dst.execute('INSERT INTO recovery_03nov.other_maestros_rows VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(sha,ordinal,tid,identical))
                        m=dst.execute('SELECT target_result_id FROM recovery_03nov.other_maestros_rows WHERE source_sha=%s AND source_ordinal=%s',(sha,ordinal)).fetchone()
                        if m['target_result_id']!=tid:raise ValueError('replay_mapping_conflict')
                    for a in source.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''',(sd['id'],)):
                        tid,ordinal,added=ids[a['award_result_id']]
                        if not added:continue
                        data={c:a[c] for c in fields['award_assignments']};data['award_result_id']=tid;data['matched_offered_position_id']=None
                        if a['matched_offered_position_id'] is not None:
                            dst.execute("INSERT INTO recovery_03nov.other_maestros_pending VALUES(%s,%s,'offer_relationship_unverified') ON CONFLICT DO NOTHING",(sha,ordinal));pending_links+=1
                        insert_row(dst,'award_assignments',data);assignments+=1
                    after=counts(dst)
                    if after['award_results']!=before['award_results']+inserted or after['award_assignments']!=before['award_assignments']+assignments:raise ValueError('write_count_mismatch')
                    if any(after[t]!=before[t] for t in before if t not in ('award_results','award_assignments')):raise ValueError('unrelated_counts_changed')
                    if dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()['n']:raise ValueError('unvalidated_foreign_keys')
                    try:
                        with dst.transaction():
                            dst.execute('DELETE FROM recovery_03nov.other_maestros_rows WHERE source_sha=%s',(sha,));raise RuntimeError('rollback_probe')
                    except RuntimeError as exc:
                        if str(exc)!='rollback_probe':raise
                    if dst.execute('SELECT count(*) AS n FROM recovery_03nov.other_maestros_rows WHERE source_sha=%s',(sha,)).fetchone()['n']!=len(rows):raise ValueError('rollback_failed')
                results.append({'version':ver,'sha256':sha,'source_rows':len(rows),'baseline_rows':check['baseline_rows'],'inserted_results':inserted,'inserted_assignments':assignments,'raw_conflicts':different,'pending_offer_links':pending_links,'before':before,'after':after})
        return {'status':'RECONCILED_SEVEN_SCOPE','documents':results,'rollback_tested':True,'personal_values_exported':False}
    finally:source.rollback()

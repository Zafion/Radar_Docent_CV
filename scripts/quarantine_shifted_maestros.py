"""Remove only this session's inserted rows where prior ordinal identity conflicts."""
import json
import psycopg
from psycopg.rows import dict_row
from scripts.recover_seven_maestros import OUT

VERSIONS=(79,138,62,243)

def run(source,socket,*,target_db='baseline17_trial'):
    if target_db not in ('baseline17_trial','baseline17_reproduction'):raise ValueError('target_identity')
    prior='baseline17_pre_seven' if target_db=='baseline17_trial' else 'baseline17_reproduction_pre_seven'
    kw={'host':str(socket),'port':55467,'user':'baseline_owner','connect_timeout':5}
    manifest={r['version_id']:r for r in json.loads((OUT/'plan_integral_reanudable.json').read_text())['entries']}
    source.row_factory=dict_row;source.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    try:
        with psycopg.connect(**kw,dbname=prior,row_factory=dict_row,options='-c default_transaction_read_only=on') as original,psycopg.connect(**kw,dbname=target_db,row_factory=dict_row) as dst:
            identity=dst.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net").fetchone()
            if identity!={'db':target_db,'data':'/work/data','net':''}:raise ValueError('target_identity')
            dst.commit();summary=[]
            with dst.transaction():
                for ver in VERSIONS:
                    sha=manifest[ver]['sha256']
                    query='''SELECT r.id,r.order_number,r.person_name_normalized FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
                    old=original.execute(query,(sha,)).fetchall();new=source.execute(query,(sha,)).fetchall()
                    old_by={r['order_number']:r for r in old};new_by={r['order_number']:r for r in new}
                    if len(old_by)!=len(old) or len(new_by)!=len(new) or old_by.keys()-new_by.keys():raise ValueError('ordinal_identity_ambiguous')
                    conflicts=sum(old_by[k]['person_name_normalized']!=new_by[k]['person_name_normalized'] for k in old_by.keys()&new_by.keys())
                    if not conflicts:raise ValueError('expected_conflict_missing')
                    missing=new_by.keys()-old_by.keys();original_ids={r['id'] for r in old};removed=0;removed_assignments=0
                    for ordinal in missing:
                        mapped=dst.execute('SELECT target_result_id FROM recovery_03nov.other_maestros_rows WHERE source_sha=%s AND source_ordinal=%s',(sha,ordinal)).fetchone()
                        if mapped:
                            tid=mapped['target_result_id']
                            if tid in original_ids:raise ValueError('refuse_delete_original')
                            actual=dst.execute('SELECT document_id,order_number FROM award_results WHERE id=%s',(tid,)).fetchone()
                            expected=dst.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(sha,)).fetchone()
                            if not actual or actual['document_id']!=expected['id'] or actual['order_number']!=ordinal:raise ValueError('inserted_row_identity_mismatch')
                            removed_assignments+=dst.execute('DELETE FROM award_assignments WHERE award_result_id=%s',(tid,)).rowcount
                            dst.execute('DELETE FROM recovery_03nov.other_maestros_rows WHERE source_sha=%s AND source_ordinal=%s',(sha,ordinal))
                            if dst.execute('DELETE FROM award_results WHERE id=%s',(tid,)).rowcount!=1:raise ValueError('delete_inserted_row_failed')
                            removed+=1
                        dst.execute("INSERT INTO recovery_03nov.other_maestros_pending VALUES(%s,%s,'shared_ordinal_person_conflict') ON CONFLICT DO NOTHING",(sha,ordinal))
                    after=dst.execute(query,(sha,)).fetchall()
                    if {r['id'] for r in after}!={r['id'] for r in old}:raise ValueError('original_rows_not_preserved')
                    summary.append({'version':ver,'shared_ordinal_conflicts':conflicts,'removed_session_rows':removed,
                                    'removed_session_assignments':removed_assignments,'pending_ordinals':len(missing)})
            return {'status':'CONFLICTS_QUARANTINED','documents':summary,'original_ids_preserved':True,'reason':'ordinal maps to different people between parser versions; name used only as conflict guard'}
    finally:source.rollback()

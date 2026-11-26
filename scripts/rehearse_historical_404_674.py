"""Reconcile only two evidenced documents on a new encrypted trial clone."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
ORIGINAL=VOLUME/'funk-439-independent-trial'
CLONE=VOLUME/'funk-404-674-independent-trial'
RECEIPT=VOLUME/'reconciliation_439_final.json'
SHAS={404:'acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0',674:'823d06203fa90d2bafefc250793dfafa7141a2b1e1dd3ed2e1ce8a506f9f9793'}


def worker():
    import psycopg
    from psycopg.rows import dict_row
    from app.services.historical_batch_reconcile import (copy_bag,copy_difficult,copy_fields,load_mixed_rows,
        plan_mixed_rows,apply_mixed_bag,apply_mixed_difficult)
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner',row_factory=dict_row)
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            x=admin.execute("SELECT current_setting('listen_addresses') AS net,current_setting('data_directory') AS data,current_setting('server_version_num') AS version").fetchone()
            if x['net'] or x['data']!='/work/data' or not 160000<=int(x['version'])<170000:raise ValueError('cluster_identity')
            if not any(x in sys.argv for x in ('--verify-only','--rollback-404','--test-mixed')):
                if admin.execute("SELECT 1 FROM pg_database WHERE datname IN ('baseline_404_674_trial','baseline_404_674_rollback')").fetchone():raise ValueError('trial_database_exists')
                admin.execute('CREATE DATABASE baseline_404_674_trial TEMPLATE baseline_439_trial')
                admin.execute('CREATE DATABASE baseline_404_674_rollback TEMPLATE baseline_439_trial')
        directory=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
        plan=json.loads((directory/'plan_integral_reanudable.json').read_text());entries={x['version_id']:x for x in plan['entries'] if x['version_id'] in SHAS}
        manifest=directory/'lote_reconciliacion_01.json';manifest_sha=hashlib.sha256(manifest.read_bytes()).hexdigest()
        if manifest_sha!='8986aa96f23992981e7e054ae52f39abab727ae10d25ad950724de2e31ae86fe':raise ValueError('manifest_sha_mismatch')
        validations={}
        for ver,file in ((404,'validacion_bolsas_grupal_56.json'),(674,'validacion_dificil_grupal_56.json')):
            report=json.loads((directory/file).read_text());validation=next(x for x in report['documents'] if x.get('version_id')==ver)
            entry=entries[ver];pdf=ROOT/Path(entry['local_sha_verified_paths'][0]).relative_to('/home/zafion/Proyects/Radar_Docent_CV')
            if pdf.is_symlink() or hashlib.sha256(pdf.read_bytes()).hexdigest()!=SHAS[ver] or entry['sha256']!=SHAS[ver] or validation.get('sha256')!=SHAS[ver] or validation.get('status')!='validated_with_limitations':raise ValueError('document_evidence_mismatch')
            if ver==404 and not validation.get('identity_multiset_matches'):raise ValueError('bag_census_unverified')
            if ver==674 and any(validation.get(k)!=0 for k in ('source_only','stored_only','ordered_differences')):raise ValueError('candidate_census_unverified')
            validations[ver]=validation
        def counters(db):
            tables=('documents','award_results','award_assignments','docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates','offered_positions','public_alerts','notification_posts','push_notification_events')
            return {t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in tables}
        def reconcile(src,dst,ver,inject=False):
            sha=SHAS[ver];family='bag_members' if ver==404 else 'difficult_candidates';entry=entries[ver];validation=validations[ver]
            with src.transaction(),dst.transaction():
                src.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');dst.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                query='SELECT d.id,d.document_version_id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'
                source=src.execute(query,(sha,)).fetchall();target=dst.execute(query,(sha,)).fetchall()
                if len(source)!=1 or len(target)!=1:raise ValueError('document_identity_ambiguous')
                sid=source[0]['id'];did=target[0]['id']
                parser=src.execute("SELECT parser_key,parser_version,rows_extracted FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(source[0]['document_version_id'],)).fetchone()
                if not parser or parser['parser_key']!=validation['parser_key'] or parser['parser_version']!=validation['parser_version'] or parser['rows_extracted']!=validation['stored_rows']:raise ValueError('parser_identity_mismatch')
                existing=dst.execute('SELECT * FROM recovery_batch.documents WHERE source_sha=%s',(sha,)).fetchone()
                if existing:
                    if existing['target_document_id']!=did:raise ValueError('existing_document_marker_conflict')
                    if ver==404:
                        n=dst.execute('SELECT count(*) AS n FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s',(did,)).fetchone()['n']
                        q=dst.execute("SELECT count(*) AS n FROM recovery_batch.quarantined_rows WHERE source_sha=%s AND kind='docent_bag_members'",(sha,)).fetchone()['n']
                        if n!=existing['source_rows'] or n+q!=existing['source_total']:raise ValueError('bag_replay_count_mismatch')
                        maps=dst.execute("SELECT source_id,target_id FROM recovery_batch.row_map WHERE kind='docent_bag_members'").fetchall()
                        src_rows=src.execute('''SELECT m.* FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id WHERE s.document_id=%s''',(sid,)).fetchall()
                        source_rows={r['id']:r for r in src_rows};member_fields=[k for k in copy_fields(src,dst,'docent_bag_members') if k!='snapshot_id']
                        checked=0
                        for m in maps:
                            if m['source_id'] not in source_rows:continue
                            target_row=dst.execute('SELECT * FROM docent_bag_members WHERE id=%s',(m['target_id'],)).fetchone();source_row=source_rows[m['source_id']]
                            if not target_row or any(source_row[k]!=target_row[k] for k in member_fields):raise ValueError('bag_replay_field_mismatch')
                            snapshot=dst.execute("SELECT target_id FROM recovery_batch.row_map WHERE kind='docent_bag_snapshots' AND source_id=%s",(source_row['snapshot_id'],)).fetchone()
                            if not snapshot or target_row['snapshot_id']!=snapshot['target_id']:raise ValueError('bag_replay_snapshot_mismatch')
                            checked+=1
                        if checked!=n:raise ValueError('bag_replay_mapping_mismatch')
                        return {'reused':n,'quarantined':q,'inserted':0}
                    n=dst.execute('SELECT count(*) AS n FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id WHERE p.document_id=%s',(did,)).fetchone()['n']
                    positions=dst.execute('SELECT count(*) AS n FROM difficult_coverage_positions WHERE document_id=%s',(did,)).fetchone()['n']
                    if n!=existing['source_rows']:raise ValueError('candidate_replay_count_mismatch')
                    posrows=src.execute('SELECT * FROM difficult_coverage_positions WHERE document_id=%s',(sid,)).fetchall();pfields=[k for k in copy_fields(src,dst,'difficult_coverage_positions') if k not in ('document_id','availability_status','availability_reason','availability_checked_at','closed_by_document_id','closed_at')]
                    cfields=[k for k in copy_fields(src,dst,'difficult_coverage_candidates') if k!='position_id'];checked=0
                    for pos in posrows:
                        pm=dst.execute("SELECT target_id FROM recovery_batch.row_map WHERE kind='difficult_coverage_positions' AND source_id=%s",(pos['id'],)).fetchone()
                        if not pm:raise ValueError('position_replay_mapping_missing')
                        pt=dst.execute('SELECT * FROM difficult_coverage_positions WHERE id=%s',(pm['target_id'],)).fetchone()
                        if not pt or any(pos[k]!=pt[k] for k in pfields) or pt['availability_status']!='unknown':raise ValueError('position_replay_field_mismatch')
                        for candidate in src.execute('SELECT * FROM difficult_coverage_candidates WHERE position_id=%s',(pos['id'],)):
                            cm=dst.execute("SELECT target_id FROM recovery_batch.row_map WHERE kind='difficult_coverage_candidates' AND source_id=%s",(candidate['id'],)).fetchone()
                            if not cm:raise ValueError('candidate_replay_mapping_missing')
                            ct=dst.execute('SELECT * FROM difficult_coverage_candidates WHERE id=%s',(cm['target_id'],)).fetchone()
                            if not ct or ct['position_id']!=pm['target_id'] or any(candidate[k]!=ct[k] for k in cfields):raise ValueError('candidate_replay_field_mismatch')
                            checked+=1
                    if len(posrows)!=positions or checked!=n:raise ValueError('difficult_replay_mapping_mismatch')
                    return {'positions_reused':positions,'candidates_reused':n,'positions_inserted':0,'candidates_inserted':0}
                if ver==404:
                    partial=bool(dst.execute('SELECT 1 FROM docent_bag_snapshots WHERE document_id=%s LIMIT 1',(did,)).fetchone())
                    if partial:
                        dst.execute('''CREATE TABLE IF NOT EXISTS recovery_batch.mixed_changes(source_sha text NOT NULL,kind text NOT NULL,
                            source_id bigint NOT NULL,target_id bigint NOT NULL,justification text NOT NULL,PRIMARY KEY(kind,source_id))''')
                        old,keys,fields=load_mixed_rows(dst,did,family);new,_,_=load_mixed_rows(src,sid,family)
                        mixed=plan_mixed_rows(old,new,keys,fields)
                        result=apply_mixed_bag(src,dst,mixed,did,sid,sha)
                        if inject:raise RuntimeError('injected_rollback_404_674')
                        return {'status':'partial_mixed','actions':result}
                    added=copy_bag(src,dst,sid,did,entry['provenance']['urls'][0],sha)
                    total=added['bag_members'];pending=added['ambiguous_rows']
                else:
                    partial=bool(dst.execute('SELECT 1 FROM difficult_coverage_positions WHERE document_id=%s LIMIT 1',(did,)).fetchone())
                    if partial:
                        dst.execute('''CREATE TABLE IF NOT EXISTS recovery_batch.mixed_changes(source_sha text NOT NULL,kind text NOT NULL,
                            source_id bigint NOT NULL,target_id bigint NOT NULL,justification text NOT NULL,PRIMARY KEY(kind,source_id))''')
                        old,keys,fields=load_mixed_rows(dst,did,family);new,_,_=load_mixed_rows(src,sid,family)
                        mixed=plan_mixed_rows(old,new,keys,fields)
                        result=apply_mixed_difficult(src,dst,mixed,did,sha)
                        if inject:raise RuntimeError('injected_rollback_404_674')
                        return {'status':'partial_mixed','actions':result}
                    added=copy_difficult(src,dst,sid,did);total=added['difficult_candidates'];pending=0
                if total+pending!=parser['rows_extracted']:raise ValueError('source_rows_unaccounted')
                dst.execute('''INSERT INTO recovery_batch.documents(source_sha,target_document_id,family,source_parser,source_parser_version,source_rows,quality,official_url,manifest_sha,source_total)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(sha,did,family,parser['parser_key'],parser['parser_version'],total,'partial_validated_with_limitations' if pending else 'validated_with_limitations',entry['provenance']['urls'][0],manifest_sha,parser['rows_extracted']))
                if inject:raise RuntimeError('injected_rollback_404_674')
                return added
        if '--test-mixed' in sys.argv:
            with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_404_674_rollback') as roll:
                a=reconcile(src,roll,404);b=reconcile(src,roll,674)
                if a.get('bag_members',0)==0 or b.get('difficult_candidates',0)==0:raise ValueError('mixed_fixture_not_initialized')
                before=counters(roll);roll.commit()
                with roll.transaction():
                    for sha,kind,table in ((SHAS[404],'docent_bag_members','docent_bag_members'),(SHAS[674],'difficult_coverage_candidates','difficult_coverage_candidates')):
                        row=roll.execute('''SELECT m.source_id,m.target_id FROM recovery_batch.row_map m
                          JOIN recovery_batch.documents d ON d.source_sha=%s WHERE m.kind=%s ORDER BY m.source_id LIMIT 1''',(sha,kind)).fetchone()
                        if not row:raise ValueError('mixed_fixture_mapping_missing')
                        roll.execute('DELETE FROM recovery_batch.row_map WHERE kind=%s AND source_id=%s',(kind,row['source_id']))
                        from psycopg import sql
                        roll.execute(sql.SQL('DELETE FROM {} WHERE id=%s').format(sql.Identifier(table)),(row['target_id'],))
                        roll.execute('DELETE FROM recovery_batch.documents WHERE source_sha=%s',(sha,))
                first404=reconcile(src,roll,404);first674=reconcile(src,roll,674)
                second404=reconcile(src,roll,404);second674=reconcile(src,roll,674)
                if first404.get('actions',{}).get('inserted')!=1 or first674.get('actions',{}).get('inserted')!=1:raise ValueError('mixed_new_rows_not_recovered')
                if second404.get('actions',{}).get('inserted',0) or second674.get('actions',{}).get('inserted',0):raise ValueError('mixed_replay_not_idempotent')
                if counters(roll)!=before:raise ValueError('mixed_fixture_counts_changed')
                print(json.dumps({'status':'MIXED_REAL_PASS','first404':first404,'first674':first674,'second404':second404,'second674':second674}))
            return
        if '--rollback-404' in sys.argv:
            with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_404_674_rollback') as roll:
                before=counters(roll);roll.commit()
                try:reconcile(src,roll,404,inject=True)
                except RuntimeError as exc:
                    if str(exc)!='injected_rollback_404_674':raise
                else:raise ValueError('rollback_not_triggered')
                after=counters(roll)
                if before!=after:raise ValueError('rollback_counts_changed')
                print(json.dumps({'status':'ROLLBACK_404_PASS','before':before,'after':after}))
            return
        if '--verify-only' in sys.argv:
            with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_404_674_trial') as dst:
                print(json.dumps({'status':'DOCUMENT_IDENTITIES_VERIFIED','404':reconcile(src,dst,404),'674':reconcile(src,dst,674)}))
            return
        results={}
        with psycopg.connect(**kw,dbname='reconstruction_dr',options='-c default_transaction_read_only=on') as src,psycopg.connect(**kw,dbname='baseline_404_674_trial') as dst,psycopg.connect(**kw,dbname='baseline_404_674_rollback') as roll:
            before=counters(dst);dst.commit();base_rollback=counters(roll);roll.commit()
            rollback_status='not_triggered'
            try:reconcile(src,roll,674,inject=True)
            except RuntimeError as exc:
                if str(exc)!='injected_rollback_404_674':raise
                rollback_status='PASS'
            except (ValueError,psycopg.Error) as exc:
                rollback_status='blocked_'+type(exc).__name__
            if counters(roll)!=base_rollback:raise ValueError('rollback_counts_changed')
            roll.commit()
            for ver in (404,674):
                try:
                    first=reconcile(src,dst,ver);second=reconcile(src,dst,ver)
                    results[str(ver)]={'status':'applied','first':first,'second':second}
                except (ValueError,psycopg.Error) as exc:
                    reason=str(exc);safe=reason if isinstance(exc,ValueError) and reason and len(reason)<80 and all(c.isascii() and (c.isalnum() or c=='_') for c in reason) else type(exc).__name__
                    results[str(ver)]={'status':'failed','reason':safe}
            after=counters(dst)
            if any(after[t]!=before[t] for t in ('documents','award_results','award_assignments','offered_positions','public_alerts','notification_posts','push_notification_events')):raise ValueError('unrelated_counts_changed')
            invalid=dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
            if invalid:raise ValueError('unvalidated_constraints')
            print(json.dumps({'status':'PARTIAL_UNTIL_INDEPENDENT_COMPARISON','documents':results,'before':before,'after':after,'rollback_674':rollback_status,'unvalidated_constraints':invalid}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    mixed='--test-mixed-existing' in sys.argv
    rollback_404='--rollback-404-existing' in sys.argv
    verifying='--verify-existing' in sys.argv or rollback_404 or mixed
    if validate_volatile_storage(minimum_bytes=(8 if verifying else 14)*1024**3)!=VOLUME:raise ValueError('insufficient_encrypted_space')
    receipt=json.loads(RECEIPT.read_text())
    if receipt.get('status')!='reconciled_with_prior_differences' or receipt.get('pending'):raise ValueError('source_receipt_invalid')
    if ORIGINAL.is_symlink() or (ORIGINAL/'data/postmaster.pid').exists():raise ValueError('source_running_or_invalid')
    if verifying:
        if not CLONE.is_dir() or CLONE.is_symlink() or (CLONE/'data/postmaster.pid').exists():raise ValueError('trial_cluster_invalid')
    else:
        if os.path.lexists(CLONE):raise ValueError('target_exists')
        shutil.copytree(ORIGINAL,CLONE,symlinks=True)
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(CLONE/'passwd'),'/etc/passwd','--ro-bind',str(CLONE/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(CLONE),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),'--gid',str(os.getgid()),'--',
         '/project/.venv/bin/python','-B','/project/scripts/rehearse_historical_404_674.py','--worker']+(['--test-mixed'] if mixed else ['--rollback-404'] if rollback_404 else ['--verify-only'] if verifying else [])
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2400)
    if p.returncode:
        print(json.dumps({'status':'STOP','condition':'isolated_worker_failed','clone_preserved':str(CLONE)}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result['clone']=str(CLONE)
    path=VOLUME/('reconciliation_404_674_mixed.json' if mixed else 'reconciliation_404_rollback.json' if rollback_404 else 'reconciliation_404_674_identity.json' if verifying else 'reconciliation_404_674_trial.json');fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())

"""Run the limited unawarded adapter on a new independent encrypted cluster."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_final_capacity'
BASE_SHA='4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b'


def worker():
    import psycopg
    from psycopg.rows import dict_row
    from app.services.historical_unawarded_reconcile import reconcile_unawarded
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner')
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        if '--certify-readonly' in sys.argv:
            from psycopg import sql
            comparisons={}
            options='-c default_transaction_read_only=on'
            with psycopg.connect(**kw,dbname='baseline',row_factory=dict_row,options=options) as original, \
                 psycopg.connect(**kw,dbname='baseline_future_trial',row_factory=dict_row,options=options) as target:
                tables=[r['table_name'] for r in original.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE' ORDER BY table_name").fetchall()]
                no_key=[]
                for table in tables:
                    cols=original.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",(table,)).fetchall()
                    names=[x['column_name'] for x in cols]
                    keyrows=original.execute('''SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
                        JOIN pg_namespace n ON n.oid=c.relnamespace JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ord) ON true
                        JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
                        WHERE n.nspname='public' AND c.relname=%s AND i.indisprimary ORDER BY k.ord''',(table,)).fetchall()
                    keys=[r['attname'] for r in keyrows]
                    if not keys:no_key.append(table);continue
                    key_sql=sql.SQL(',').join(map(sql.Identifier,keys))
                    sort_sql=sql.SQL(',').join(sql.SQL('{}::text COLLATE "C"').format(sql.Identifier(k)) for k in keys)
                    query=sql.SQL('SELECT {},to_jsonb(t) AS row FROM {} t ORDER BY {}').format(key_sql,sql.Identifier(table),sort_sql)
                    left=original.cursor(name='old_'+table);right=target.cursor(name='new_'+table)
                    left.execute(query);right.execute(query)
                    def stream(cursor):
                        while batch:=cursor.fetchmany(1000):yield from batch
                    olds=iter(stream(left));news=iter(stream(right))
                    old=next(olds,None);new=next(news,None);matched=missing=changed=0
                    while old is not None:
                        identity=lambda r:tuple(str(r[k]) for k in keys)
                        while new is not None and identity(new)<identity(old):new=next(news,None)
                        if new is None or identity(new)!=identity(old):missing+=1
                        elif {k:new['row'].get(k) for k in names}!=old['row']:changed+=1
                        else:matched+=1
                        old=next(olds,None)
                    left.close();right.close()
                    comparisons[table]={'baseline_rows_matched':matched,'baseline_rows_missing':missing,'baseline_rows_changed':changed}
                identities=target.execute('''SELECT count(*) AS n,count(DISTINCT v.sha256) AS sha_count,
                    count(*) FILTER (WHERE v.sha256 IS NULL) AS missing_sha FROM documents d LEFT JOIN document_versions v ON v.id=d.document_version_id''').fetchone()
                plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
                wanted=(700,436,701,1000391,1000067,1000444)
                entries={x['version_id']:x['sha256'] for x in plan['entries'] if x['version_id'] in wanted}
                if len(entries)!=len(wanted):raise ValueError('document_manifest_incomplete')
                documents={}
                for version,sha in entries.items():
                    row=target.execute('''SELECT count(DISTINCT d.id) AS documents,count(DISTINCT r.id) AS results,count(a.id) AS assignments
                      FROM documents d JOIN document_versions v ON v.id=d.document_version_id
                      LEFT JOIN award_results r ON r.document_id=d.id LEFT JOIN award_assignments a ON a.award_result_id=r.id
                      WHERE v.sha256=%s''',(sha,)).fetchone()
                    documents[str(version)]={'sha256':sha,**row}
                totals={name:target.execute('SELECT count(*) AS n FROM '+name).fetchone()['n'] for name in ('documents','award_results','award_assignments','offered_positions','public_alerts','notification_posts','push_notification_events')}
                experts=target.execute("SELECT count(*) AS n,count(*) FILTER(WHERE result_status='unawarded_no_requests') AS unawarded FROM historical_unawarded_expert_vacancies").fetchone()
                print(json.dumps({'status':'OBSERVATION_COMPLETE','baseline_comparisons':comparisons,'baseline_tables':tables,'baseline_tables_without_primary_key':no_key,'document_identities':identities,'documents':documents,'totals':totals,'experts':experts}))
            return
        if '--verify-results' in sys.argv:
            plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
            e={r['version_id']:r['sha256'] for r in plan['entries'] if r['version_id'] in (700,436,701,1000067,1000444)}
            with psycopg.connect(**kw,dbname='baseline_future_trial',row_factory=dict_row) as db:
                matches=db.execute('''SELECT v.sha256,count(*) AS n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id
                    JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id
                    WHERE a.matched_offered_position_id IS NOT NULL AND v.sha256=ANY(%s) GROUP BY v.sha256''',([e[700],e[701],e[1000067],e[1000444]],)).fetchall()
                counts_by_sha={r['sha256']:r['n'] for r in matches}
                quarantine=db.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n']
                qrows=db.execute('SELECT document_sha,reason,count(*) AS n FROM recovery_unawarded.quarantine WHERE document_sha=ANY(%s) GROUP BY document_sha,reason',([e[700],e[436]],)).fetchall()
                relations=db.execute("SELECT procedure_key,count(*) AS n FROM historical_procedure_documents GROUP BY procedure_key").fetchall()
                invalid=db.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
                db.commit()
                try:
                    with db.transaction():
                        db.execute('DELETE FROM recovery_quarantine.positions_19nov WHERE (source_sha,source_id)=(SELECT source_sha,source_id FROM recovery_quarantine.positions_19nov LIMIT 1)')
                        raise RuntimeError('injected_rollback')
                except RuntimeError as exc:
                    if str(exc)!='injected_rollback':raise
                after=db.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n']
                if counts_by_sha.get(e[700],0)!=93 or counts_by_sha.get(e[701],0)!=120 or counts_by_sha.get(e[1000067],0) or counts_by_sha.get(e[1000444],0) or quarantine!=598 or after!=quarantine or invalid:
                    raise ValueError('verification_counts_mismatch')
                print(json.dumps({'status':'VERIFY_PASS','matches_700':93,'matches_701':120,'matches_19nov':0,'offer_quarantine_rows':quarantine,
                                  'rollback':'PASS','unvalidated_constraints':invalid,'procedure_documents':relations,'review_quarantine':qrows}))
            return
        if '--inspect-offers' in sys.argv:
            from collections import Counter
            from dataclasses import fields
            from app.services.offered_positions_parser import OfferedPositionsParserService,ParsedOfferedPosition
            plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
            entries={x['version_id']:x for x in plan['entries'] if x['version_id'] in (1000005,1000535)}
            names=[x.name for x in fields(ParsedOfferedPosition)];result={}
            with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row,options='-c default_transaction_read_only=on') as src:
                for ver,entry in entries.items():
                    p=ROOT/Path(entry['local_sha_verified_paths'][0]).relative_to('/home/zafion/Proyects/Radar_Docent_CV')
                    if hashlib.sha256(p.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('offer_sha_mismatch')
                    parsed=OfferedPositionsParserService()._parse_document({'file_path':str(p),'list_scope':'secundaria_otros'})
                    rows=src.execute('''SELECT o.* FROM offered_positions o JOIN documents d ON d.id=o.document_id
                        JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(entry['sha256'],)).fetchall()
                    differing=[k for k in names if Counter(str(getattr(r,k)) for r in parsed)!=Counter(str(r[k]) for r in rows)]
                    result[str(ver)]={'pdf_rows':len(parsed),'source_rows':len(rows),'fields_with_different_multisets':differing,
                                      'pdf_source_types':sorted({str(r.source_type) for r in parsed}),
                                      'stored_source_types':sorted({str(r['source_type']) for r in rows})}
            print(json.dumps({'status':'OFFER_FIELDS_INSPECTED','documents':result}));return
        if '--run-coordinator' in sys.argv:
            command=['/project/.venv/bin/python','-B','/project/scripts/run_historical_cumulative.py',
                     '--socket','/work/socket','--port','55467','--source-db','reconstruction_dr',
                     '--destination-db','baseline_future_trial','--user','baseline_owner',
                     '--work-root','/work','--receipt-dir','/work']
            def invoke(mode):
                p=subprocess.run(command+['--mode',mode],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2400)
                if p.returncode not in (0,2):
                    try: detail=json.loads(p.stderr.strip().splitlines()[-1])
                    except (ValueError,IndexError):detail={'status':'failed'}
                    return {'status':'failed','exit_code':p.returncode,'error_type':detail.get('error_type')}
                return json.loads(p.stdout.strip().splitlines()[-1])
            def counts():
                with psycopg.connect(**kw,dbname='baseline_future_trial',row_factory=dict_row,options='-c default_transaction_read_only=on') as db:
                    return {name:db.execute('SELECT count(*) AS n FROM '+name).fetchone()['n'] for name in ('documents','award_results','award_assignments','offered_positions','public_alerts','notification_posts','push_notification_events')}
            before=counts();plan=invoke('plan')
            if plan['status']!='planned':print(json.dumps({'status':'COORDINATOR_PLAN_FAILED','plan':plan,'before':before}));return
            first=invoke('apply');after=counts()
            if any(before[t]!=after[t] for t in ('public_alerts','notification_posts','push_notification_events')):raise ValueError('notification_count_changed')
            if first.get('status')=='failed':print(json.dumps({'status':'COORDINATOR_FAILED','plan':plan,'first':first,'before':before,'after':after}));return
            details=json.loads(Path(first['receipt']).read_text())['report']
            if any(r.get('status')=='failed' for r in details.get('documents',{}).values()) or any(r.get('status')=='failed' for r in details.get('quarantine',[])):
                print(json.dumps({'status':'COORDINATOR_PARTIAL','plan':plan,'first':first,'before':before,'after':after}));return
            second=invoke('apply');end=counts()
            print(json.dumps({'status':'COORDINATOR_REPLAY_CHECK','plan':plan,'first':first,'second':second,'before':before,'after':after,'after_replay':end,'counts_idempotent':after==end}))
            return
        backup=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/backup_antes_etiquetas_20261123/historical.dump'
        if backup.is_symlink() or hashlib.sha256(backup.read_bytes()).hexdigest()!='b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a':raise ValueError('source_backup_mismatch')
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            if '--prepare-only' in sys.argv:
                existing=admin.execute("SELECT datname FROM pg_database WHERE datname IN ('reconstruction_dr','baseline_future_trial')").fetchall()
                if existing:raise ValueError('trial_database_already_exists')
            admin.execute('CREATE DATABASE reconstruction_dr TEMPLATE template0')
            subprocess.run(['/usr/lib/postgresql/16/bin/pg_restore','--no-owner','--no-acl','--exit-on-error','--single-transaction',
              '-h','/work/socket','-p','55467','-U','baseline_owner','-d','reconstruction_dr',str(backup)],check=True,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1800)
            admin.execute('CREATE DATABASE baseline_future_trial TEMPLATE baseline')
        if '--prepare-only' in sys.argv:
            plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
            required=(700,436,701,702,1000391,1000067,1000444,1000005,1000535)
            entries={r['version_id']:r for r in plan['entries'] if r['version_id'] in required}
            if set(entries)!=set(required):raise ValueError('document_manifest_incomplete')
            with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row,options='-c default_transaction_read_only=on') as src:
                docs=src.execute('''SELECT v.sha256,count(*) AS n FROM documents d JOIN document_versions v ON v.id=d.document_version_id
                    WHERE v.sha256=ANY(%s) GROUP BY v.sha256''',([entries[x]['sha256'] for x in required],)).fetchall()
                if len(docs)!=len(required) or any(r['n']!=1 for r in docs):raise ValueError('reconstruction_document_identity_missing')
                n=src.execute('SELECT count(*) AS n FROM award_results').fetchone()['n']
                if not n:raise ValueError('reconstruction_empty')
            with psycopg.connect(**kw,dbname='baseline_future_trial') as dst:
                for name in ('20261122_document_validation_metadata.sql','20261122_historical_procedures.sql'):
                    dst.execute((ROOT/'app/storage/migrations'/name).read_text())
                dst.commit()
            print(json.dumps({'status':'SOURCES_PREPARED','source_document_identities':len(docs),'source_award_results':n,
                              'source_database':'reconstruction_dr','destination_database':'baseline_future_trial'}))
            return
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
        entry=next(e for e in plan['entries'] if e['version_id']==700)
        relative=Path(entry['local_sha_verified_paths'][0]).relative_to('/home/zafion/Proyects/Radar_Docent_CV')
        pdf=ROOT/relative
        with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row) as src, psycopg.connect(**kw,dbname='baseline_future_trial',row_factory=dict_row) as dst:
            before={t:dst.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in ('award_results','award_assignments','public_alerts','notification_posts','push_notification_events')};dst.commit()
            args=dict(document_sha=entry['sha256'],pdf_path=pdf,family='maestros')
            first=reconcile_unawarded(src,dst,**args);second=reconcile_unawarded(src,dst,**args)
            after={t:dst.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in before}
            q=dst.execute('SELECT count(*) AS n FROM recovery_unawarded.quarantine').fetchone()['n']
            if second.get('inserted',0) or any(after[t]!=before[t] for t in before if t!='award_results'):raise ValueError('idempotence_or_unrelated_change')
            if after['award_results']!=before['award_results']+first.get('inserted',0):raise ValueError('result_count_mismatch')
        print(json.dumps({'status':'PARTIAL_DOCUMENT_RECONCILED','document_version':700,'first':first,'second':second,
                          'before':before,'after':after,'quarantine':q,'assignments':'not_integrated','rollback':'synthetic_only'}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage,restore_ciphertext
    from app.services.encrypted_baseline_copy import encrypt_copy
    if '--prepare-existing' in sys.argv or '--run-existing' in sys.argv or '--inspect-offers-existing' in sys.argv or '--verify-existing' in sys.argv or '--certify-existing' in sys.argv:
        return prepare_existing()
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=12*1024**3)!=VOLUME:raise ValueError('work_volume_invalid')
    source=ROOT/'auditoria_local/recuperacion_17nov_luks/baseline_21nov_20261124/baseline.dump'
    st=source.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or st.st_size!=71320284:raise ValueError('baseline_identity')
    temp=VOLUME/('.unawarded_cipher_'+uuid.uuid4().hex)
    try:
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as handle:encrypted=encrypt_copy(handle,temp,BASE_SHA,71320284)
        work,result=restore_ciphertext(temp,encrypted['key'],BASE_SHA,71320284)
    finally:temp.unlink(missing_ok=True)
    if result.get('status')!='RESTORED':raise ValueError('baseline_restore_failed')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc',
         '--dev','/dev','--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd',
         '--ro-bind',str(work/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(work),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),
         '--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/rehearse_unawarded_21nov.py','--worker']
    process=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1200)
    if process.returncode:
        print(json.dumps({'status':'STOP','condition':'isolated_worker_failed','cluster_preserved':str(work),'automatic_retry':False}));return 1
    result=json.loads(process.stdout.strip().splitlines()[-1]);result['cluster']=str(work)
    target=VOLUME/'unawarded_21nov_receipt.json';fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as handle:json.dump(result,handle,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps(result));return 0


def prepare_existing():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    volume=ROOT/'auditoria_local/recovery_19nov_capacity'
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(volume)
    if validate_volatile_storage(minimum_bytes=6*1024**3)!=volume:raise ValueError('work_volume_invalid')
    receipt=json.loads((volume/'baseline_21nov.restore.json').read_text());work=Path(receipt['work_directory'])
    if receipt.get('status')!='BASELINE_RESTORED' or receipt.get('source_sha256')!=BASE_SHA or work.parent!=volume or work.is_symlink() or (work/'data/postmaster.pid').exists():raise ValueError('restored_cluster_invalid')
    running='--run-existing' in sys.argv
    inspecting='--inspect-offers-existing' in sys.argv
    verifying='--verify-existing' in sys.argv
    certifying='--certify-existing' in sys.argv
    target=volume/(('cumulative_execution_'+uuid.uuid4().hex+'.json') if running else ('offer_fields_'+uuid.uuid4().hex+'.json') if inspecting else ('cumulative_verify_'+uuid.uuid4().hex+'.json') if verifying else ('cumulative_observation_'+uuid.uuid4().hex+'.json') if certifying else 'cumulative_sources_prepare.json')
    if os.path.lexists(target):raise ValueError('prepare_receipt_exists')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc',
         '--dev','/dev','--ro-bind','/sys','/sys','--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd',
         '--ro-bind',str(work/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(work),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),
         '--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/rehearse_unawarded_21nov.py','--worker','--run-coordinator' if running else '--inspect-offers' if inspecting else '--verify-results' if verifying else '--certify-readonly' if certifying else '--prepare-only']
    proc=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1800)
    if proc.returncode:
        last=proc.stderr.strip().splitlines()[-1] if proc.stderr.strip() else ''
        category=last.split(':',1)[0] if last and all(c.isalnum() or c in '._' for c in last.split(':',1)[0]) else 'isolated_prepare_failed'
        if last.startswith('ValueError: '):
            reason=last[len('ValueError: '):]
            if reason and len(reason)<80 and all(c.isascii() and (c.isalnum() or c=='_') for c in reason):category=reason
        print(json.dumps({'status':'STOP','condition':category,'cluster_preserved':str(work),'automatic_retry':False}));return 1
    result=json.loads(proc.stdout.strip().splitlines()[-1]);result['cluster']=str(work)
    fd=os.open(target,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())

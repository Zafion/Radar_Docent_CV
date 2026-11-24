"""Read-only comparison of locally owned historical clusters by document SHA."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from types import SimpleNamespace

import psycopg

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.historical_persistent_cluster import HistoricalCluster

OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
EXPECTED='980862fae66d76344a5649b348524c57e0c1660de5784bd58fe1e93f5264852e'
RECEIPT=Path(os.environ.get('FUNK_BASELINE_RECEIPT',str(OUT/'baseline_copy_e59524419125bab68a813343/receipt.json')))
QUERIES={
 'award_results':"SELECT v.sha256,count(*) FROM award_results r JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
 'award_assignments':"SELECT v.sha256,count(*) FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id JOIN documents d ON d.id=r.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
 'offered_positions':"SELECT v.sha256,count(*) FROM offered_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
 'bag_members':"SELECT v.sha256,count(*) FROM docent_bag_members m JOIN docent_bag_snapshots s ON s.id=m.snapshot_id JOIN documents d ON d.id=s.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
 'difficult_positions':"SELECT v.sha256,count(*) FROM difficult_coverage_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
 'difficult_candidates':"SELECT v.sha256,count(*) FROM difficult_coverage_candidates c JOIN difficult_coverage_positions p ON p.id=c.position_id JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id GROUP BY v.sha256",
}

def inspect(db):
    db.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
    versions={r[0] for r in db.execute('SELECT sha256 FROM document_versions')}
    values={name:dict(db.execute(query)) for name,query in QUERIES.items()}
    db.rollback();return versions,values

def main():
    global WORK,RECEIPT,EXPECTED
    dr='--baseline17-dr' in sys.argv
    is17='--baseline17' in sys.argv or dr
    if is17:
        if any(arg not in ('--baseline17','--baseline17-dr','--compare-rows','--reconcile17','--integrate0519-17','--register03-17','--diagnose03-17','--recover-maestros17','--recover-secondary17','--reproduce-clean17','--resume-clean17','--review-seven17','--review-seven-keys17','--recover-seven17','--quarantine-shifted17','--reproduce-fresh17','--resume-fresh17') for arg in sys.argv[1:]):raise ValueError('baseline17_scope_invalid')
        if '--reconcile17' in sys.argv and '--compare-rows' in sys.argv:raise ValueError('separate_comparison_and_reconciliation')
        EXPECTED='2abcde4ebb9914b41164885048d53c7aca6da3f5c141b3371a306e2048642d35'
        RECEIPT=ROOT/('auditoria_local/recuperacion_17nov_luks/baseline_17nov.dr.restore.json' if dr else 'auditoria_local/recuperacion_17nov_luks/baseline_17nov.restore.json')
    st=RECEIPT.lstat()
    if (not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600
        or (not is17 and (RECEIPT.name!='receipt.json' or not RECEIPT.resolve().is_relative_to(OUT.resolve())
        or not RECEIPT.parent.name.startswith('baseline_copy_')))):raise ValueError('receipt_path_identity')
    receipt=json.loads(RECEIPT.read_text())
    WORK=Path(receipt.get('work_directory' if is17 else 'volatile_cluster',''))
    from scripts.restore_baseline_isolated import validate_volatile_storage
    permitted_root=validate_volatile_storage(minimum_bytes=0)
    if (receipt.get('status')!='BASELINE_RESTORED' or receipt.get('source_sha256')!=EXPECTED
        or WORK.parent!=permitted_root or not WORK.name.startswith('funk-baseline-')):raise ValueError('receipt_mismatch')
    for path in (WORK,WORK/'data',WORK/'socket'):
        st=path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise ValueError('cluster_path_identity')
    if (WORK/'data/postmaster.pid').exists() or (WORK/'data/PG_VERSION').read_text().strip()!='16':raise ValueError('cluster_not_stopped_or_wrong_version')
    short_socket=None
    if is17:
        short_socket=tempfile.TemporaryDirectory(prefix='funk17socket-',dir='/tmp')
        os.symlink(WORK/'socket',Path(short_socket.name)/'socket')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent',
         '--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--bind',str(WORK),'/work','--dir','/etc',
         '--ro-bind',str(WORK/'passwd'),'/etc/passwd','--ro-bind',str(WORK/'group'),'/etc/group',
         '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/bin/sh','-c',
         '/usr/lib/postgresql/16/bin/pg_ctl -D /work/data -l /dev/null -w start >/dev/null || exit 1; '
         'printf "READY\\n"; read line; /usr/lib/postgresql/16/bin/pg_ctl -D /work/data -m fast -w stop >/dev/null']
    proc=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    try:
        if proc.stdout.readline().strip()!='READY':raise RuntimeError('baseline_start_failed')
        plan=(OUT/'plan_integral_reanudable.json').read_bytes()
        if dr:
            from psycopg.conninfo import make_conninfo
            path=OUT/'backup_antes_etiquetas_20261123/historical.dump'
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest()!='b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a':raise ValueError('reconstruction_backup_identity')
            sock=str(Path(short_socket.name)/'socket')
            marker=WORK/'reconstruction_dr.restored.json'
            with psycopg.connect(host=sock,port=55467,user='baseline_owner',dbname='postgres',autocommit=True) as admin:
                exists=admin.execute("SELECT 1 FROM pg_database WHERE datname='reconstruction_dr'").fetchone()
                if exists and (not marker.is_file() or json.loads(marker.read_text()).get('source_sha256')!='b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a'):raise ValueError('reconstruction_restore_uncertain_no_retry')
                if not exists:admin.execute('CREATE DATABASE reconstruction_dr TEMPLATE template0')
            if not exists:
                restore=subprocess.run(['/usr/lib/postgresql/16/bin/pg_restore','--no-owner','--no-acl','--exit-on-error','--single-transaction','--host',sock,'--port','55467','--username','baseline_owner','--dbname','reconstruction_dr',str(path)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=1800)
                if restore.returncode:raise ValueError('reconstruction_backup_restore_failed')
                fd=os.open(marker,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
                with os.fdopen(fd,'w') as f:json.dump({'status':'RESTORED','source_sha256':'b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a'},f);f.flush();os.fsync(f.fileno())
            def verify_dr(c):
                identity=c.execute("SELECT current_database(),current_setting('data_directory'),current_setting('listen_addresses')").fetchone()
                if identity!=('reconstruction_dr','/work/data',''):raise ValueError('dr_reconstruction_identity')
                c.rollback()
            cluster_context=nullcontext(SimpleNamespace(dsn=lambda:make_conninfo(host=sock,port=55467,user='baseline_owner',dbname='reconstruction_dr'),verify=verify_dr))
        else:cluster_context=HistoricalCluster(ROOT/'auditoria_local/pgv_hist_20261120',hashlib.sha256(plan).hexdigest())
        with cluster_context as cluster:
            with psycopg.connect(host=str(Path(short_socket.name)/'socket' if short_socket else WORK/'socket'),port=55467,user='baseline_owner',dbname='baseline',connect_timeout=5) as old, psycopg.connect(cluster.dsn()) as new:
                cluster.verify(new)
                ident=old.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses'),current_setting('server_version_num')").fetchone();old.rollback()
                if ident!=('/work/data','','160015'):raise ValueError('baseline_identity')
                a,counts_a=inspect(old);b,counts_b=inspect(new)
                row_report=None
                trial_report=None
                trial_api_report=None
                position_diagnosis=None
                functional_report=None
                source_report=None
                integration_0519=None
                batch_report=None
                batch_02=None
                duplicate_guard=None
                batch17=None
                procedure17=None
                procedure03=None
                seven17=None
                recoveryseven=None
                shifted=None
                if '--quarantine-shifted17' in sys.argv:
                    from scripts.quarantine_shifted_maestros import run
                    socket=Path(short_socket.name)/'socket'
                    shifted={}
                    for target in ('baseline17_trial','baseline17_reproduction'):
                        shifted[target]=run(new,socket,target_db=target)
                        replay=run(new,socket,target_db=target)
                        if any(r['removed_session_rows'] for r in replay['documents']):raise ValueError('quarantine_replay_changed')
                if '--recover-seven17' in sys.argv:
                    from scripts.recover_seven_maestros import run
                    socket=Path(short_socket.name)/'socket'
                    recoveryseven=run(new,socket)
                    recoveryseven['second_execution']=run(new,socket)
                    if any(d['inserted_results'] or d['inserted_assignments'] for d in recoveryseven['second_execution']['documents']):raise ValueError('seven_replay_changed_rows')
                if '--review-seven17' in sys.argv:
                    from scripts.review_seven_maestros import run
                    seven17=run(new,Path(short_socket.name)/'socket')
                if '--review-seven-keys17' in sys.argv:
                    from scripts.review_seven_maestros import run
                    seven17=run(new,Path(short_socket.name)/'socket',keys_only=True)
                clean17=None
                if '--reproduce-clean17' in sys.argv or '--resume-clean17' in sys.argv or '--reproduce-fresh17' in sys.argv or '--resume-fresh17' in sys.argv:
                    from scripts.reconcile_batch_trial import run as run_batch
                    from scripts.integrate_0519_trial import run as run_0519
                    from scripts.integrate_03nov_baseline17 import run as run_03
                    from scripts.recover_maestros_baseline17 import run as run_maestros,recover_secondary
                    from scripts.recover_seven_maestros import run as run_seven
                    socket=Path(short_socket.name)/'socket';fresh='--reproduce-fresh17' in sys.argv or '--resume-fresh17' in sys.argv;target='baseline17_fresh' if fresh else 'baseline17_reproduction'
                    if fresh and '--resume-fresh17' not in sys.argv:old.close()  # Template must have no connections.
                    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname='postgres',autocommit=True) as admin:
                        exists=bool(admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(target,)).fetchone())
                        resuming='--resume-clean17' in sys.argv or '--resume-fresh17' in sys.argv
                        if exists and not resuming:raise ValueError('reproduction_target_exists')
                        if not exists and resuming:raise ValueError('reproduction_target_missing')
                        if not exists:
                            from psycopg import sql
                            admin.execute(sql.SQL('CREATE DATABASE {} TEMPLATE {}').format(sql.Identifier(target),sql.Identifier('baseline' if fresh else 'baseline17_trial_prebatch')))
                    if fresh and not resuming:
                        with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname=target) as db:
                            for migration in ('20261122_document_validation_metadata.sql','20261122_historical_procedures.sql'):
                                db.execute((ROOT/'app/storage/migrations'/migration).read_text())
                            db.execute('CREATE SCHEMA recovery_17_review')
                            db.execute('CREATE TABLE recovery_17_review.documents (source_sha text PRIMARY KEY,source_version bigint NOT NULL,target_document_id bigint,reason text NOT NULL)')
                            for manifest in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
                                for entry in json.loads((OUT/manifest).read_text())['documents']:
                                    match=db.execute('SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s',(entry['sha256'],)).fetchone()
                                    db.execute('INSERT INTO recovery_17_review.documents VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING',(entry['sha256'],entry['version_id'],match[0] if match else None,'pending_cross_baseline_row_identity_and_provenance'))
                            db.commit()
                    clean17={'status':'IN_PROGRESS','source':'baseline' if fresh else 'baseline17_trial_prebatch','target':target,'steps':[],
                             'resumed':resuming}
                    if clean17['resumed']:clean17['resume_context']='Existing isolated reproduction resumed; consult prior receipts for earlier stops and changes.'
                    def twice(label,fn):
                        first=fn()
                        from scripts.integrate_03nov_trial import counts
                        from psycopg.rows import dict_row
                        with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname=target,row_factory=dict_row,options='-c default_transaction_read_only=on') as db:before=counts(db)
                        second=fn()
                        with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname=target,row_factory=dict_row,options='-c default_transaction_read_only=on') as db:after=counts(db)
                        if before!=after:raise ValueError('reproduction_idempotence_failed_'+label)
                        clean17['steps'].append({'step':label,'first':first,'second':second})
                    for manifest in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
                        twice(manifest,lambda m=manifest:run_batch(new,socket,OUT/m,target_db=target,baseline17=True))
                    twice('0519',lambda:run_0519(new,socket,baseline17=True,target_db=target))
                    twice('03_document_metadata',lambda:run_03(new,socket,target_db=target))
                    twice('maestros',lambda:run_maestros(new,socket,target_db=target))
                    twice('secundaria',lambda:recover_secondary(new,socket,target_db=target))
                    twice('five_additional_maestros',lambda:run_seven(new,socket,target_db=target))
                    with psycopg.connect(host=str(socket),port=55467,user='baseline_owner',dbname=target,options='-c default_transaction_read_only=on') as check:
                        expected={'documents':778,'award_results':593161,'award_assignments':48077,'offered_positions':74842,'docent_bag_members':61144,'difficult_coverage_positions':367,'difficult_coverage_candidates':34601,'public_alerts':194,'notification_posts':776,'push_notification_events':204}
                        actual={t:check.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in expected}
                        if actual!=expected:raise ValueError('reproduction_final_count_mismatch')
                    clean17.update(status='REPRODUCED',counts=actual)
                maestros17=None
                secondary17=None
                if '--recover-secondary17' in sys.argv:
                    from scripts.recover_maestros_baseline17 import recover_secondary
                    socket=Path(short_socket.name)/'socket'
                    secondary17=recover_secondary(new,socket)
                    secondary17['second_execution']=recover_secondary(new,socket)
                    if secondary17['after']!=secondary17['second_execution']['after']:raise ValueError('secondary_replay_changed_counts')
                if '--recover-maestros17' in sys.argv:
                    from scripts.recover_maestros_baseline17 import run
                    socket=Path(short_socket.name)/'socket'
                    maestros17=run(new,socket)
                    maestros17['second_execution']=run(new,socket)
                    if maestros17['after']!=maestros17['second_execution']['after']:raise ValueError('maestros_replay_changed_counts')
                diagnosis03=None
                if '--diagnose03-17' in sys.argv:
                    from scripts.diagnose_maestros_03 import run
                    diagnosis03=run(new,Path(short_socket.name)/'socket')
                if '--register03-17' in sys.argv:
                    from scripts.integrate_03nov_baseline17 import run
                    socket=Path(short_socket.name)/'socket'
                    procedure03=run(new,socket)
                    procedure03['second_execution']=run(new,socket)
                    if procedure03['after']!=procedure03['second_execution']['after']:raise ValueError('procedure03_replay_changed_counts')
                if '--integrate0519-17' in sys.argv:
                    from scripts.integrate_0519_trial import run
                    socket=Path(short_socket.name)/'socket'
                    procedure17=run(new,socket,baseline17=True)
                    procedure17['second_execution']=run(new,socket,baseline17=True)
                if '--reconcile17' in sys.argv:
                    from scripts.reconcile_batch_trial import run
                    socket=Path(short_socket.name)/'socket'
                    batch17=[]
                    for name in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
                        manifest=OUT/name
                        first=run(new,socket,manifest,target_db='baseline17_trial',baseline17=True)
                        second=run(new,socket,manifest,target_db='baseline17_trial',baseline17=True)
                        if first['after']!=second['after']:raise ValueError('baseline17_replay_changed_counts')
                        batch17.append({'manifest':name,'first':first,'second':second})
                if '--compare-rows' in sys.argv:
                    from app.services.historical_database_row_compare import compare_all
                    old.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    row_report=compare_all(old,new,a&b)
                    old.rollback();new.rollback()
                if '--trial' in sys.argv:
                    from scripts.reconcile_baseline_trial import run
                    trial_report=run(old,new,WORK/'socket')
                if '--trial-check' in sys.argv:
                    from scripts.reconcile_baseline_trial import check_api,SHA_ROWS
                    evidence=json.loads((OUT/'validacion_procedimientos_final_20261122.json').read_text())
                    selected={x['sha256']:x for x in evidence['documents'] if x.get('status')=='validated_extraction' and x['sha256'] in SHA_ROWS}
                    if set(selected)!=set(SHA_ROWS):raise ValueError('validation_evidence_missing')
                    for sha,doc in selected.items():
                        actual=new.execute('''SELECT p.parser_key,p.parser_version,p.rows_extracted FROM document_parse_runs p
                          JOIN document_versions v ON v.id=p.document_version_id WHERE v.sha256=%s AND p.status='success'
                          ORDER BY p.finished_at DESC NULLS LAST,p.id DESC LIMIT 1''',(sha,)).fetchone()
                        expected=doc['parse_run']
                        if actual!=(expected['parser_key'],expected['parser_version'],expected['rows_extracted']):raise ValueError('validation_parser_mismatch')
                    new.rollback()
                    trial_api_report=check_api(WORK/'socket')
                    trial_api_report['validated_document_parser_versions_verified']=len(selected)
                    trial_api_report['parser_code_sha256_attested']=False
                if '--positions-diagnosis' in sys.argv:
                    from scripts.diagnose_positions_comparison import run
                    old.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    new.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                    position_diagnosis=run(old,new,a&b)
                    old.rollback();new.rollback()
                if '--functional-03' in sys.argv:
                    from scripts.integrate_03nov_trial import run,check_api,ensure_document_evidence,verify_integrity
                    functional_report=run(new,WORK/'socket')
                    functional_report['second_execution']=run(new,WORK/'socket')
                    functional_report['document_evidence']=ensure_document_evidence(WORK/'socket')
                    functional_report['document_evidence_replay']=ensure_document_evidence(WORK/'socket')
                    functional_report['integrity']=verify_integrity(WORK/'socket')
                    functional_report['api']=check_api(new,WORK/'socket')
                if '--sources-03' in sys.argv:
                    from scripts.inspect_03nov_sources import run
                    source_report=run(old,new)
                if '--functional-0519' in sys.argv:
                    from scripts.integrate_0519_trial import run,check_api
                    from scripts.integrate_03nov_trial import verify_integrity
                    integration_0519=run(new,WORK/'socket')
                    integration_0519['second_execution']=run(new,WORK/'socket')
                    integration_0519['integrity_03nov']=verify_integrity(WORK/'socket')
                    integration_0519['api']=check_api(WORK/'socket')
                if '--batch-01' in sys.argv:
                    from scripts.reconcile_batch_trial import run,check_api
                    from scripts.integrate_03nov_trial import verify_integrity
                    batch_report=run(new,WORK/'socket')
                    batch_report['second_execution']=run(new,WORK/'socket')
                    batch_report['integrity_03nov']=verify_integrity(WORK/'socket')
                    batch_report['api']=check_api(new,WORK/'socket')
                if '--batch-02' in sys.argv:
                    from scripts.reconcile_batch_trial import run,check_api
                    from scripts.integrate_03nov_trial import verify_integrity
                    manifest=OUT/'lote_reconciliacion_02.json'
                    batch_02=run(new,WORK/'socket',manifest)
                    batch_02['second_execution']=run(new,WORK/'socket',manifest)
                    batch_02['integrity_03nov']=verify_integrity(WORK/'socket')
                    batch_02['api']=check_api(new,WORK/'socket')
                if '--duplicate-guard' in sys.argv:
                    from scripts.verify_batch_duplicate_guards import run
                    duplicate_guard=run(new,WORK/'socket')
        rows=[]
        for sha in sorted(a|b):
            rows.append({'sha256':sha,'document_presence':'both' if sha in a and sha in b else ('baseline_only' if sha in a else 'reconstruction_only'),
                         'counts':{name:{'baseline':counts_a[name].get(sha,0),'reconstruction':counts_b[name].get(sha,0)} for name in QUERIES}})
        summary={name:{'baseline':sum(counts_a[name].values()),'reconstruction':sum(counts_b[name].values()),
                       'same_sha_same_count':sum(counts_a[name].get(sha,0)==counts_b[name].get(sha,0) and (counts_a[name].get(sha,0)>0 or counts_b[name].get(sha,0)>0) for sha in a&b),
                       'same_sha_different_count':sum(counts_a[name].get(sha,0)!=counts_b[name].get(sha,0) for sha in a&b)} for name in QUERIES}
        report={'status':'DOCUMENT_SHA_COUNT_COMPARISON','baseline_date':'2026-11-17' if is17 else '2026-10-31','baseline_sha256':EXPECTED,
                'document_versions':{'baseline':len(a),'reconstruction':len(b),'both':len(a&b),'baseline_only':len(a-b),'reconstruction_only':len(b-a)},
                'families':summary,'document_rows':rows,'read_only':True,'personal_data_exported':False,
                'limitations':['Equal counts do not establish identical records.','SHA presence does not establish publication date or production absence.',
                               'Person identity and individual rows have not been compared.']}
        dest=OUT/('comparacion_reconstruccion_baseline17_dr_20261124.json' if dr else 'comparacion_reconstruccion_baseline17_20261124.json' if is17 else 'comparacion_baseline_restaurado_20261123.json');tmp=dest.with_suffix('.tmp')
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(tmp,dest)
        print(json.dumps({k:v for k,v in report.items() if k!='document_rows'}))
        if row_report is not None:
            dest=OUT/('comparacion_filas_baseline17_20261124.json' if is17 else 'comparacion_filas_baseline_20261123.json');tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(row_report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps({k:v for k,v in row_report.items() if k!='documents'}))
        if trial_report is not None:
            dest=OUT/'ensayo_reconciliacion_cuarentena_20261123.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(trial_report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(trial_report))
        if trial_api_report is not None:
            dest=OUT/'api_ensayo_cuarentena_20261123.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(trial_api_report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(trial_api_report))
        if position_diagnosis is not None:
            dest=OUT/'diagnostico_campos_plazas_20261123.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(position_diagnosis,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(position_diagnosis))
        if functional_report is not None:
            dest=OUT/'integracion_funcional_03nov_20261123.json';tmp=dest.with_suffix('.tmp')
            if dest.exists() and functional_report.get('status')=='INTEGRATION_REPLAY_NOOP':
                prior=json.loads(dest.read_text());prior['latest_verification']=functional_report;functional_report=prior
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(functional_report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(functional_report))
        if source_report is not None:print(json.dumps({'source_identities':source_report}))
        if integration_0519 is not None:
            dest=OUT/'integracion_funcional_0519_20261123.json';tmp=dest.with_suffix('.tmp')
            if dest.exists() and integration_0519.get('status')=='INTEGRATION_0519_REPLAY_NOOP':
                prior=json.loads(dest.read_text());prior['latest_verification']=integration_0519;integration_0519=prior
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(integration_0519,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(integration_0519))
        if batch_report is not None:
            dest=OUT/'reconciliacion_lote_01_20261123.json';tmp=dest.with_suffix('.tmp')
            if dest.exists():
                prior=json.loads(dest.read_text())
                if any(d.get('outcome')=='inserted' for d in batch_report['documents']):
                    prior.setdefault('additional_applications',[]).append(batch_report)
                else:prior['latest_verification']=batch_report
                batch_report=prior
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(batch_report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(batch_report))
        if batch_02 is not None:
            dest=OUT/'reconciliacion_lote_02_20261123.json';tmp=dest.with_suffix('.tmp')
            if dest.exists() and not any(d.get('outcome')=='inserted' for d in batch_02['documents']):
                prior=json.loads(dest.read_text());prior['latest_verification']=batch_02;batch_02=prior
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(batch_02,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(batch_02))
        if duplicate_guard is not None:
            dest=OUT/'verificacion_duplicados_404_20261123.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(duplicate_guard,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(duplicate_guard))
        if batch17 is not None:
            dest=OUT/'reconciliacion_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump({'status':'BATCH17_COMPLETE','batches':batch17},f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps({'status':'BATCH17_COMPLETE','batches':len(batch17),'after':batch17[-1]['second']['after']}))
        if procedure17 is not None:
            dest=OUT/'integracion_0519_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(procedure17,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(procedure17))
        if procedure03 is not None:
            dest=OUT/'integracion_03_parcial_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(procedure03,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest)
            print(json.dumps(procedure03))
        if diagnosis03 is not None:
            dest=OUT/'claves_03_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(diagnosis03,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(diagnosis03))
        if maestros17 is not None:
            dest=OUT/'recuperacion_maestros_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(maestros17,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(maestros17))
        if secondary17 is not None:
            dest=OUT/'recuperacion_secundaria_baseline17_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(secondary17,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(secondary17))
        if clean17 is not None:
            dest=OUT/('reproduccion_desastre_baseline17_20261124.json' if dr else 'reproduccion_desde_baseline17_20261124.json' if '--reproduce-fresh17' in sys.argv or '--resume-fresh17' in sys.argv else 'reproduccion_limpia_baseline17_20261124.json');tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(clean17,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps({'status':clean17['status'],'steps':len(clean17['steps']),'counts':clean17['counts']}))
        if seven17 is not None:
            dest=OUT/('claves_siete_maestros_20261124.json' if '--review-seven-keys17' in sys.argv else 'revision_siete_maestros_20261124.json');tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(seven17,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(seven17))
        if recoveryseven is not None:
            dest=OUT/'recuperacion_siete_maestros_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(recoveryseven,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(recoveryseven))
        if shifted is not None:
            dest=OUT/'cuarentena_ordinales_desplazados_20261124.json';tmp=dest.with_suffix('.tmp')
            fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'w') as f:json.dump(shifted,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
            os.replace(tmp,dest);print(json.dumps(shifted))
    finally:
        if proc.poll() is None:
            try:proc.stdin.write('stop\n');proc.stdin.flush();proc.wait(timeout=30)
            except Exception:proc.terminate();proc.wait(timeout=10)
        if short_socket:short_socket.cleanup()
    if proc.returncode:raise RuntimeError('baseline_stop_failed')

if __name__=='__main__':
    try:main()
    except Exception as e:
        import traceback
        frame=traceback.extract_tb(e.__traceback__)[-1]
        print(json.dumps({'status':'STOP','error':str(e) if isinstance(e,(ValueError,RuntimeError)) else type(e).__name__,'location':frame.name,'line':frame.lineno}),file=sys.stderr);sys.exit(1)

"""Reconcile only Maestros 700 on new disposable DBs in the isolated trial."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_final_capacity'
OFFER_SHA='33e69ded5cd9c7ef44032e749d1893ca7bd2717fca0ad99c864e7708992693b4'


def worker():
    import psycopg
    from psycopg.rows import dict_row
    from app.services.historical_unawarded_reconcile import reconcile_unawarded
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    kw=dict(host='/work/socket',port=55467,user='baseline_owner')
    partial='--resume-partial' in sys.argv or '--verify-full' in sys.argv
    full_name=('baseline_700_full_trial' if '--verify-full' in sys.argv else 'baseline_future_trial') if partial else 'baseline_700_full_trial_v2'
    rollback_name='baseline_700_rollback_trial' if partial else 'baseline_700_rollback_trial_v2'
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            for name in (full_name,rollback_name):
                exists=bool(admin.execute('SELECT 1 FROM pg_database WHERE datname=%s',(name,)).fetchone())
                if exists != partial:raise ValueError('trial_database_identity')
            if not partial:
                admin.execute('CREATE DATABASE baseline_700_full_trial_v2 TEMPLATE baseline')
                admin.execute('CREATE DATABASE baseline_700_rollback_trial_v2 TEMPLATE baseline')
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
        items={x['version_id']:x for x in plan['entries'] if x['version_id'] in (700,702)}
        if set(items)!={700,702} or items[702]['sha256']!=OFFER_SHA:raise ValueError('offer_evidence_missing')
        base=Path('/home/zafion/Proyects/Radar_Docent_CV')
        paths={k:ROOT/Path(v['local_sha_verified_paths'][0]).relative_to(base) for k,v in items.items()}
        if paths[702].is_symlink() or hashlib.sha256(paths[702].read_bytes()).hexdigest()!=OFFER_SHA:raise ValueError('offer_pdf_sha_mismatch')
        args=dict(document_sha=items[700]['sha256'],pdf_path=paths[700],family='maestros',include_awards=True,accredited_offer_sha=OFFER_SHA)
        with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row) as src:
            linked=src.execute('''SELECT count(*) AS n FROM historical_procedure_documents pd JOIN documents d ON d.id=pd.document_id
              JOIN document_versions v ON v.id=d.document_version_id WHERE pd.procedure_key='interino-2026-11-03' AND v.sha256=ANY(%s)''',
              ([items[700]['sha256'],OFFER_SHA],)).fetchone()['n']
            src.rollback()
            if linked!=2:raise ValueError('procedure_document_relationship_missing')
            tables=('award_results','award_assignments','public_alerts','notification_posts','push_notification_events')
            def counts(db):return {t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in tables}
            with psycopg.connect(**kw,dbname=rollback_name,row_factory=dict_row) as test:
                before_test=counts(test)
                if not partial:
                    test.execute("CREATE FUNCTION fail_700_assignment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected rollback'; END $$")
                    test.execute('CREATE TRIGGER fail_700_assignment BEFORE INSERT ON award_assignments FOR EACH ROW EXECUTE FUNCTION fail_700_assignment()')
                elif not test.execute("SELECT 1 FROM pg_trigger WHERE tgname='fail_700_assignment' AND NOT tgisinternal").fetchone():
                    raise ValueError('rollback_trigger_missing')
                test.commit()
                try:reconcile_unawarded(src,test,**args)
                except psycopg.errors.RaiseException:pass
                else:raise ValueError('rollback_injection_not_triggered')
                if counts(test)!=before_test:raise ValueError('rollback_did_not_restore_counts')
                test.commit()
            with psycopg.connect(**kw,dbname=full_name,row_factory=dict_row) as dst:
                before=counts(dst);dst.commit()
                first=reconcile_unawarded(src,dst,**args)
                second=reconcile_unawarded(src,dst,**args)
                after=counts(dst)
                q=dst.execute('SELECT ordinal,reason FROM recovery_unawarded.quarantine WHERE document_sha=%s ORDER BY ordinal,reason',(items[700]['sha256'],)).fetchall()
                fk=dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
                if second.get('inserted',0) or second.get('awards_inserted',0) or second.get('associations_inserted',0):raise ValueError('replay_not_idempotent')
                if any(before[t]!=after[t] for t in ('public_alerts','notification_posts','push_notification_events')) or fk:raise ValueError('notification_or_integrity_changed')
                if after['award_results']!=before['award_results']+first.get('inserted',0) or after['award_assignments']!=before['award_assignments']+first.get('awards_inserted',0):raise ValueError('count_mismatch')
                categories=first.get('inserted',0)+first.get('reused',0)+first.get('quarantined',0)+first.get('not_eligible',0)
                if categories!=6416:raise ValueError('document_category_count_mismatch')
            print(json.dumps({'status':'MAESTROS_700_RECONCILED','first':first,'second':second,'before':before,'after':after,
                              'quarantine':q,'unvalidated_constraints':fk,'rollback_injected':'PASS','offer_sha_verified':True}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=8*1024**3)!=VOLUME:raise ValueError('encrypted_space_insufficient')
    receipt=json.loads((VOLUME/'unawarded_21nov_receipt.json').read_text());work=Path(receipt['cluster'])
    if receipt.get('status')!='PARTIAL_DOCUMENT_RECONCILED' or work.parent!=VOLUME or not work.name.startswith('funk-baseline-') or (work/'data/postmaster.pid').exists():raise ValueError('trial_cluster_identity')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
       '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev',
       '--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
       '--ro-bind',str(ROOT),'/project','--bind',str(work),'/work','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
       '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/rehearse_maestros_700_full.py','--worker']
    if '--resume-partial' in sys.argv:cmd.append('--resume-partial')
    if '--verify-full' in sys.argv:cmd.append('--verify-full')
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1800)
    if p.returncode:
        # No record content or SQL is emitted on failure.
        lines=p.stderr.strip().splitlines();category=lines[-1].split(':',1)[0] if lines else 'worker_failed'
        print(json.dumps({'status':'STOP','condition':category,'trial_cluster':str(work),'automatic_retry':False}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result['trial_cluster']=str(work)
    receipt_name='maestros_700_full_receipt_final.json' if '--verify-full' in sys.argv else 'maestros_700_full_receipt_v2.json'
    fd=os.open(VOLUME/receipt_name,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as handle:json.dump(result,handle,indent=2);handle.write('\n');handle.flush();os.fsync(handle.fileno())
    print(json.dumps(result));return 0


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())

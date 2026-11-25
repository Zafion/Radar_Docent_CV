"""Reconcile only Maestros 700 and 436 on a new encrypted local clone."""
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
    progress={'status':'IN_PROGRESS','steps':[]}
    def save():
        temp=Path('/work/maestros_cumulative_progress.tmp');target=Path('/work/maestros_cumulative_progress.json')
        with temp.open('w') as f:json.dump(progress,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temp,target)
    def count(db):
        names=('award_results','award_assignments','public_alerts','notification_posts','push_notification_events')
        return {t:db.execute('SELECT count(*) AS n FROM '+t).fetchone()['n'] for t in names}
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as admin:
            if admin.execute("SELECT 1 FROM pg_database WHERE datname='baseline_maestros_cumulative'").fetchone():raise ValueError('trial_database_exists')
            admin.execute('CREATE DATABASE baseline_maestros_cumulative TEMPLATE baseline')
        save()
        plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
        entries={r['version_id']:r for r in plan['entries'] if r['version_id'] in (700,702,436)}
        if set(entries)!={700,702,436} or entries[702]['sha256']!=OFFER_SHA:raise ValueError('document_evidence_missing')
        old_root=Path('/home/zafion/Proyects/Radar_Docent_CV')
        paths={k:ROOT/Path(v['local_sha_verified_paths'][0]).relative_to(old_root) for k,v in entries.items()}
        if paths[702].is_symlink() or hashlib.sha256(paths[702].read_bytes()).hexdigest()!=OFFER_SHA:raise ValueError('offer_pdf_sha_mismatch')
        with psycopg.connect(**kw,dbname='reconstruction_dr',row_factory=dict_row) as src, psycopg.connect(**kw,dbname='baseline_maestros_cumulative',row_factory=dict_row) as dst:
            links=src.execute('''SELECT count(*) AS n FROM historical_procedure_documents pd JOIN documents d ON d.id=pd.document_id
              JOIN document_versions v ON v.id=d.document_version_id WHERE pd.procedure_key='interino-2026-11-03' AND v.sha256=ANY(%s)''',
              ([entries[700]['sha256'],OFFER_SHA],)).fetchone()['n'];src.rollback()
            if links!=2:raise ValueError('offer_procedure_evidence_missing')
            progress['before']=count(dst);dst.commit();save()
            for version in (700,436):
                args=dict(document_sha=entries[version]['sha256'],pdf_path=paths[version],family='maestros',include_awards=True,
                          accredited_offer_sha=OFFER_SHA if version==700 else None)
                if version==436:
                    before=count(dst);dst.execute("CREATE FUNCTION fail_436_assignment() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'injected rollback 436'; END $$")
                    dst.execute('CREATE TRIGGER fail_436_assignment BEFORE INSERT ON award_assignments FOR EACH ROW EXECUTE FUNCTION fail_436_assignment()');dst.commit()
                    try:reconcile_unawarded(src,dst,**args)
                    except psycopg.errors.RaiseException as exc:
                        if exc.diag.message_primary!='injected rollback 436':raise
                    else:raise ValueError('rollback_injection_not_triggered')
                    if count(dst)!=before:raise ValueError('rollback_counts_changed')
                    dst.execute('DROP TRIGGER fail_436_assignment ON award_assignments');dst.execute('DROP FUNCTION fail_436_assignment()');dst.commit()
                    progress['rollback_436']='PASS';save()
                first=reconcile_unawarded(src,dst,**args);second=reconcile_unawarded(src,dst,**args)
                if any(second.get(k,0) for k in ('inserted','awards_inserted','associations_inserted')):raise ValueError('replay_not_idempotent')
                before_src=first.get('inserted',0)+first.get('reused',0)+first.get('quarantined',0)+first.get('not_eligible',0)
                if not before_src:raise ValueError('empty_source_categories')
                quarantine=dst.execute('SELECT reason,count(*) AS n FROM recovery_unawarded.quarantine WHERE document_sha=%s GROUP BY reason ORDER BY reason',(entries[version]['sha256'],)).fetchall()
                dst.commit()
                progress['steps'].append({'version':version,'sha256':entries[version]['sha256'],'source_rows_accounted':before_src,'first':first,'second':second,'quarantine':quarantine});save()
            after=count(dst)
            fk=dst.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
            if fk or any(after[t]!=progress['before'][t] for t in ('public_alerts','notification_posts','push_notification_events')):raise ValueError('notification_or_constraint_mismatch')
            progress.update(status='MAESTROS_CUMULATIVE_WITH_PENDING_REVIEW',after=after,unvalidated_constraints=fk);save()
        print(json.dumps(progress))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=8*1024**3)!=VOLUME:raise ValueError('encrypted_space_insufficient')
    receipt=json.loads((VOLUME/'unawarded_21nov_receipt.json').read_text());work=Path(receipt['cluster'])
    if receipt.get('status')!='PARTIAL_DOCUMENT_RECONCILED' or work.parent!=VOLUME or not work.name.startswith('funk-baseline-') or (work/'data/postmaster.pid').exists():raise ValueError('trial_cluster_identity')
    command=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
       '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev',
       '--tmpfs','/tmp','--dir','/etc','--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
       '--ro-bind',str(ROOT),'/project','--bind',str(work),'/work','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin',
       '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/project/.venv/bin/python','-B','/project/scripts/rehearse_maestros_700_436.py','--worker']
    p=subprocess.run(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=2400)
    if p.returncode:
        lines=p.stderr.strip().splitlines();last=lines[-1] if lines else ''
        category=last if last.startswith(('ValueError: ', 'RuntimeError: ')) and len(last)<160 else 'isolated_worker_failed'
        print(json.dumps({'status':'STOP','condition':category,'cluster':str(work),'progress_path':str(work/'maestros_cumulative_progress.json'),'automatic_retry':False}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result['cluster']=str(work)
    fd=os.open(VOLUME/'maestros_700_436_receipt.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.exit(main())

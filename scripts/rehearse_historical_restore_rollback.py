"""Isolated migration and full database recreation from the local baseline template."""
import json,os,stat,subprocess,sys,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
RECEIPT=OUT/'baseline_copy_aa61e931996820a11baba529/receipt.json'
SHA='980862fae66d76344a5649b348524c57e0c1660de5784bd58fe1e93f5264852e'

def launch():
    info=RECEIPT.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600:raise ValueError('receipt_identity')
    r=json.loads(RECEIPT.read_text());w=Path(r['volatile_cluster'])
    if r.get('source_sha256')!=SHA or r.get('status')!='BASELINE_RESTORED' or w.parent!=Path('/dev/shm') or not w.name.startswith('funk-baseline-'):raise ValueError('baseline_identity')
    for p in (w,w/'data',w/'socket'):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:raise ValueError('cluster_identity')
    if (w/'data/postmaster.pid').exists():raise ValueError('cluster_not_stopped')
    v=os.statvfs('/dev/shm')
    if v.f_bavail*v.f_frsize<2*1024**3:raise ValueError('insufficient_volatile_space')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent',
         '--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc','--ro-bind',str(w/'passwd'),'/etc/passwd',
         '--ro-bind',str(w/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(w),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),'--gid',str(os.getgid()),
         '--','/project/.venv/bin/python','-B','/project/scripts/rehearse_historical_restore_rollback.py','--worker']
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,timeout=900)
    if not p.stdout:raise RuntimeError('no_result')
    report=json.loads(p.stdout.strip().splitlines()[-1]);print(json.dumps(report),flush=True)
    if p.returncode or report.get('status')!='PASS':raise RuntimeError('rehearsal_failed')
    path=OUT/'ensayo_recreacion_baseline_20261123.json'
    tmp=path.with_suffix('.tmp');fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def worker():
    import psycopg
    from psycopg import sql
    os.umask(0o077)
    if os.getuid()==0 or len(Path('/proc/net/route').read_text().splitlines())>1:raise ValueError('isolation')
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False;name='rollback_check_'+uuid.uuid4().hex[:12];created=False
    report={'status':'STOP','network':'private_namespace','source':'baseline','clone':name}
    kw={'host':'/work/socket','port':55467,'user':'baseline_owner','connect_timeout':5}
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        def snapshot(db):
            with psycopg.connect(**kw,dbname=db,options='-c default_transaction_read_only=on') as c:
                tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
                counts={t[0]:c.execute(sql.SQL('SELECT count(*) FROM public.{}').format(sql.Identifier(t[0]))).fetchone()[0] for t in tables}
                columns=c.execute("SELECT table_name,column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
                return {'counts':counts,'columns':columns}
        original=snapshot('baseline')
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as c:
            ident=c.execute("SELECT current_setting('data_directory'),current_setting('listen_addresses')").fetchone()
            if ident!=('/work/data',''):raise ValueError('database_identity')
            c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE baseline').format(sql.Identifier(name)));created=True
        if snapshot(name)!=original:raise ValueError('initial_clone_mismatch')
        with psycopg.connect(**kw,dbname=name) as c:
            for migration in ('20261122_document_validation_metadata.sql','20261122_historical_procedures.sql'):
                c.execute((Path('/project/app/storage/migrations')/migration).read_text())
            c.commit()
            if not c.execute("SELECT to_regclass('public.historical_procedures') IS NOT NULL").fetchone()[0]:raise ValueError('migration_missing')
        with psycopg.connect(**kw,dbname='postgres',autocommit=True) as c:
            c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)));created=False
            c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE baseline').format(sql.Identifier(name)));created=True
        if snapshot(name)!=original or snapshot('baseline')!=original:raise ValueError('recreated_baseline_mismatch')
        report.update(status='PASS',migrations=2,public_tables=len(original['counts']),counts=original['counts'],
                      rollback='database_recreated_from_unchanged_baseline_template',schema_columns_match=True,
                      source_counts_match=True,scope='migration_only; reconciler was not rerun during this rollback test')
    finally:
        if created:
            try:
                with psycopg.connect(**kw,dbname='postgres',autocommit=True) as c:c.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
            except Exception:report['status']='STOP';report['cleanup']='failed'
        stopped=not started
        if started:
            stopped=subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL).returncode==0
            if not stopped:report['status']='STOP'
        report['postgres_stopped']=stopped
        print(json.dumps(report),flush=True)

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:launch()

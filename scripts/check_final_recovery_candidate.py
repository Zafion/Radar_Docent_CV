"""Inspect the final local candidate in a network-isolated, read-only session."""
import hashlib,json,os,stat,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
WORK=VOLUME/'funk-404-674-independent-trial'
RECEIPT=VOLUME/'reconciliation_404_674_final_v3.json'
SHA='9b9ecbea01806d2f59c79c7d3906e551e25f1821da41beeebec2841b321538da'

def worker():
    import psycopg
    from psycopg.rows import dict_row
    pg='/usr/lib/postgresql/16/bin/pg_ctl';started=False
    try:
        subprocess.run([pg,'-D','/work/data','-l','/dev/null','-w','start'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);started=True
        with psycopg.connect(host='/work/socket',port=55467,user='baseline_owner',dbname='baseline_404_674_trial',row_factory=dict_row,options='-c default_transaction_read_only=on') as db:
            ident=db.execute("SELECT current_database() AS db,current_setting('data_directory') AS data,current_setting('listen_addresses') AS net,current_setting('transaction_read_only') AS ro,current_setting('server_version_num') AS version").fetchone()
            if ident!={'db':'baseline_404_674_trial','data':'/work/data','net':'','ro':'on','version':'160015'}:raise ValueError('database_identity')
            names=('documents','award_results','award_assignments','offered_positions','docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates','public_alerts','notification_posts','push_notification_events')
            counts={name:db.execute('SELECT count(*) AS n FROM '+name).fetchone()['n'] for name in names}
            expected=(778,593161,48077,74842,61144,367,34601,194,776,204)
            if tuple(counts.values())!=expected:raise ValueError('counts_mismatch')
            docs=db.execute('''SELECT count(*) AS n,count(DISTINCT v.sha256) AS sha FROM documents d JOIN document_versions v ON v.id=d.document_version_id''').fetchone()
            invalid=db.execute("SELECT count(*) AS n FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated").fetchone()['n']
            q19=db.execute('SELECT count(*) AS n FROM recovery_quarantine.positions_19nov').fetchone()['n']
            q404=db.execute("SELECT count(*) AS n FROM recovery_batch.quarantined_rows WHERE source_sha='acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0'").fetchone()['n']
            q439=db.execute("SELECT count(*) AS n FROM recovery_batch.quarantined_rows WHERE source_sha='2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4'").fetchone()['n']
            groups=db.execute('''SELECT p.availability_status,count(*) AS n FROM difficult_coverage_positions p JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256='823d06203fa90d2bafefc250793dfafa7141a2b1e1dd3ed2e1ce8a506f9f9793' GROUP BY p.availability_status''').fetchall()
            roles=db.execute('''SELECT count(*) AS n FROM pg_roles r WHERE r.rolcanlogin AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
              AND has_database_privilege(r.oid,current_database(),'CONNECT') AND has_table_privilege(r.oid,'public.documents','SELECT')
              AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%' AND c.relkind IN ('r','p','v','m','f','S') AND has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER'))
              AND NOT EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' AND has_schema_privilege(r.oid,n.oid,'CREATE'))''').fetchone()['n']
            if docs!={'n':778,'sha':778} or invalid or (q19,q404,q439)!=(598,16,8) or groups!=[{'availability_status':'unknown','n':52}]:raise ValueError('integrity_or_quarantine_mismatch')
            print(json.dumps({'status':'PASS','identity':ident,'counts':counts,'documents':docs,'unvalidated_constraints':invalid,'quarantine':{'19nov':q19,'404':q404,'439':q439},'version674':groups,'candidate_read_roles':roles}))
    finally:
        if started:subprocess.run([pg,'-D','/work/data','-m','fast','-w','stop'],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)

def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=2*1024**3)!=VOLUME:raise ValueError('encrypted_volume_invalid')
    if RECEIPT.is_symlink() or hashlib.sha256(RECEIPT.read_bytes()).hexdigest()!=SHA or json.loads(RECEIPT.read_text())['status']!='reconciled_with_documentary_limits':raise ValueError('receipt_mismatch')
    for p in (WORK,WORK/'data',WORK/'socket'):
        s=p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or stat.S_IMODE(s.st_mode)!=0o700:raise ValueError('cluster_permissions')
    if (WORK/'data/postmaster.pid').exists() or (WORK/'data/PG_VERSION').read_text().strip()!='16':raise ValueError('cluster_not_stopped')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
         '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc',
         '--ro-bind',str(WORK/'passwd'),'/etc/passwd','--ro-bind',str(WORK/'group'),'/etc/group','--ro-bind',str(ROOT),'/project','--bind',str(WORK),'/work',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--uid',str(os.getuid()),'--gid',str(os.getgid()),'--',
         '/project/.venv/bin/python','-B','/project/scripts/check_final_recovery_candidate.py','--worker']
    p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=900)
    if p.returncode:
        last=p.stderr.strip().splitlines()[-1] if p.stderr.strip() else ''
        reason=last[len('ValueError: '):] if last.startswith('ValueError: ') else 'isolated_check_failed'
        if not reason or len(reason)>80 or not all(c.isascii() and (c.isalnum() or c=='_') for c in reason):reason='isolated_check_failed'
        print(json.dumps({'status':'STOP','reason':reason}));return 1
    result=json.loads(p.stdout.strip().splitlines()[-1]);result.update(cluster=str(WORK),source_receipt_sha256=SHA)
    path=VOLUME/'final_readonly_candidate_check.json';fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(result));return 0

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:sys.path.insert(0,str(ROOT));sys.exit(main())

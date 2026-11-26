"""Validate the exact restored artifact against the attested candidate in isolation."""
import hashlib,json,os,re,stat,subprocess,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V=ROOT/'auditoria_local/recovery_19nov_capacity'
EXPORT=V/'final_candidate_export_20261126_v3'
OLD=V/'funk-404-674-independent-trial'
NEW=V/'funk-final-artifact-restored-20261126'

def worker():
    sys.path.insert(0,'/project')
    import psycopg
    from psycopg import sql
    from scripts.compare_backup_contents import compare,snapshot
    with snapshot('old') as a, snapshot('new') as b:
        schemas=lambda db: [r[0] for r in db.execute("SELECT nspname FROM pg_namespace WHERE nspname NOT LIKE 'pg_%' AND nspname<>'information_schema' ORDER BY 1")]
        sa,sb=schemas(a),schemas(b)
        if sa!=sb:raise ValueError('schema_names_differ')
        comparison={s:compare(a,b,s) for s in sa}
        # compare() rejects empty inventories; candidate namespaces have tables.
        queries={
          'indexes':"SELECT n.nspname,c.relname,pg_get_indexdef(c.oid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='i' AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2",
          'constraints':"SELECT n.nspname,c.relname,k.conname,k.contype,k.convalidated,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2,3",
          'triggers':"SELECT n.nspname,c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE NOT t.tgisinternal AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2,3",
          'views':"SELECT schemaname,viewname,definition FROM pg_views WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY 1,2",
          'materialized_views':"SELECT schemaname,matviewname,definition FROM pg_matviews WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY 1,2",
          'functions':"SELECT n.nspname,p.oid::regprocedure::text,pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE p.prokind='f' AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2",
          'extensions':"SELECT e.extname,e.extversion,n.nspname FROM pg_extension e JOIN pg_namespace n ON n.oid=e.extnamespace ORDER BY 1",
          'sequences':"SELECT schemaname,sequencename,data_type,start_value,min_value,max_value,increment_by,cycle,cache_size,last_value FROM pg_sequences WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY 1,2"}
        metadata={}
        for name,q in queries.items():
            x,y=a.execute(q).fetchall(),b.execute(q).fetchall()
            metadata[name]={'equal':x==y,'old_count':len(x),'new_count':len(y)}
        def sequence_states(db):
            names=db.execute("SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.relkind='S' AND n.nspname NOT LIKE 'pg_%' AND n.nspname<>'information_schema' ORDER BY 1,2").fetchall()
            return [(s,n,*db.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(s,n))).fetchone()) for s,n in names]
        x,y=sequence_states(a),sequence_states(b)
        metadata['sequence_state']={'equal':x==y,'old_count':len(x),'new_count':len(y)}
        invalid=b.execute("SELECT (SELECT count(*) FROM pg_index WHERE NOT indisvalid OR NOT indisready),(SELECT count(*) FROM pg_constraint WHERE contype IN ('f','c') AND NOT convalidated)").fetchone()
        checks={}
        for name,q in {
            'documents':'SELECT count(*) FROM documents', 'award_results':'SELECT count(*) FROM award_results',
            'award_assignments':'SELECT count(*) FROM award_assignments','docent_bag_members':'SELECT count(*) FROM docent_bag_members',
            'difficult_positions':'SELECT count(*) FROM difficult_coverage_positions','difficult_candidates':'SELECT count(*) FROM difficult_coverage_candidates',
            'quarantine_19':'SELECT count(*) FROM recovery_quarantine.positions_19nov',
            'alerts':'SELECT count(*) FROM public_alerts','posts':'SELECT count(*) FROM notification_posts','push':'SELECT count(*) FROM push_notification_events'}.items():
            checks[name]=b.execute(q).fetchone()[0]
        expected={'documents':778,'award_results':593161,'award_assignments':48077,'docent_bag_members':61144,'difficult_positions':367,'difficult_candidates':34601,'quarantine_19':598,'alerts':194,'posts':776,'push':204}
        status=all(v['status']=='equal' for v in comparison.values()) and all(v['equal'] for v in metadata.values()) and invalid==(0,0) and checks==expected
        print(json.dumps({'status':'PASS' if status else 'DIFFERENCE','schemas':comparison,'metadata':metadata,'invalid_indexes_and_constraints':invalid,'counts':checks,
          'limits':'Owners and ACLs intentionally omitted on restore; role separately verified; sequences compared at rest.'}))

def main():
    receipt=json.loads((EXPORT/'export_receipt.json').read_text())
    fd=os.open(EXPORT/'candidate.dump',os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
    if digest!=receipt['sha256'] or json.loads((EXPORT/'restore_receipt.json').read_text())['sha256']!=digest:raise ValueError('artifact_identity')
    for w in (OLD,NEW):
        if (w/'data/postmaster.pid').exists():raise ValueError('cluster_running')
        for p in (w,w/'data',w/'socket'):
            st=p.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise ValueError('cluster_permissions')
    common=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent','--ro-bind','/usr','/usr',
      '--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin','--proc','/proc','--dev','/dev',
      '--tmpfs','/tmp','--dir','/etc','--ro-bind',str(OLD/'passwd'),'/etc/passwd','--ro-bind',str(OLD/'group'),'/etc/group',
      '--ro-bind',str(ROOT),'/project','--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin']
    compare_cmd=common+['--setenv','PGSERVICEFILE','/tmp/pg_service.conf','--overlay-src',str(OLD),'--tmp-overlay','/left',
       '--overlay-src',str(NEW),'--tmp-overlay','/right','--','/bin/bash','-c',r'''set -eu
printf '[old]\nhost=/left/socket\nport=55467\nuser=baseline_owner\ndbname=baseline_404_674_trial\n[new]\nhost=/right/socket\nport=55467\nuser=baseline_owner\ndbname=baseline\n' >/tmp/pg_service.conf
pg=/usr/lib/postgresql/16/bin/pg_ctl
cleanup() { "$pg" -D /right/data -m fast -w stop >/dev/null 2>&1 || :; "$pg" -D /left/data -m fast -w stop >/dev/null 2>&1 || :; }
trap cleanup EXIT
"$pg" -D /left/data -o '-c unix_socket_directories=/left/socket' -l /tmp/left.log -w start >/dev/null
"$pg" -D /right/data -o '-c unix_socket_directories=/right/socket' -l /tmp/right.log -w start >/dev/null
cd /project
/project/.venv/bin/python -B /project/scripts/validate_final_candidate_artifact.py --worker
''']
    api_cmd=common+['--overlay-src',str(NEW),'--tmp-overlay','/work','--chdir','/project','--','/project/.venv/bin/python','-B','-c',
       "from scripts.compare_17nov_isolated import api_worker; api_worker('baseline',browser_check=False,hardened_role=True,recovery_loopback=True,restored_candidate=True)"]
    outputs=[]
    for cmd in (compare_cmd,api_cmd):
        p=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,timeout=1800)
        if p.returncode:
            matches=re.findall(r'(?:ValueError|RuntimeError): ([A-Za-z0-9_]+)',p.stderr)
            raise RuntimeError(matches[-1] if matches else 'isolated_validation_failed')
        outputs.append(json.loads(p.stdout.strip().splitlines()[-1]))
        if outputs[-1]['status']!='PASS':
            print(json.dumps(outputs[-1]),file=sys.stderr)
            raise ValueError('validation_difference')
    fs=os.statvfs(V);free=fs.f_bavail*fs.f_frsize
    if free<6*1024**3:raise ValueError('insufficient_remaining_margin')
    result={'status':'VALIDATED','artifact_sha256':digest,'artifact_size_bytes':receipt['size_bytes'],
       'artifact':str(EXPORT/'candidate.dump'),'restored_cluster':str(NEW),'comparison':outputs[0],'api':outputs[1],
       'space_free_bytes':free,'limitations':['Restore uses --no-owner --no-acl; destination ownership and reader grants must be applied and verified separately.']}
    fd=os.open(EXPORT/'final_validation_receipt_v3.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps({'status':result['status'],'receipt':str(EXPORT/'final_validation_receipt_v3.json'),'artifact_sha256':digest,'artifact_size_bytes':receipt['size_bytes'],'space_free_bytes':free,'metadata':outputs[0]['metadata']}))

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()

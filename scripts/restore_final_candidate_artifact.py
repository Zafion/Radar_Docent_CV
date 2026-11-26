"""Restore the attested candidate dump in a new isolated LUKS cluster."""
import hashlib,json,os,signal,stat,subprocess,sys,time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
V=ROOT/'auditoria_local/recovery_19nov_capacity'
EXPORT=V/'final_candidate_export_20261126_v3'
SOURCE=EXPORT/'candidate.dump'
W=V/'funk-final-artifact-restored-20261126'
GIB=1024**3

def free():
    s=os.statvfs(V);return s.f_bavail*s.f_frsize

def sha(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()

def worker():
    pg='/usr/lib/postgresql/16/bin/'
    env={'PATH':'/usr/bin:/bin','HOME':'/work','TMPDIR':'/work/tmp','LANG':'C.UTF-8','PGPASSFILE':'/dev/null','PGSERVICEFILE':'/dev/null','ENABLE_NOTIFICATIONS':'0'}
    def run(args):
        p=subprocess.run([pg+args[0],*args[1:]],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=1800)
        if p.returncode:
            Path('/work/restore_error.log').write_bytes(p.stderr)
            raise RuntimeError('command_failed_'+args[0])
    Path('/work/socket').mkdir(mode=0o700);Path('/work/tmp').mkdir(mode=0o700)
    run(['initdb','-D','/work/data','-U','baseline_owner','--encoding=UTF8','--locale=en_US.UTF-8','--auth-local=trust','--auth-host=reject'])
    Path('/work/data/postgresql.conf').write_text("listen_addresses=''\nport=55467\nunix_socket_directories='/work/socket'\nunix_socket_permissions=0700\nshared_buffers='32MB'\nmax_connections=10\nlog_statement='none'\nlog_min_error_statement=panic\ntemp_file_limit='512MB'\nmax_wal_size='1GB'\n")
    started=False
    try:
        run(['pg_ctl','-D','/work/data','-l','/dev/null','-w','start']);started=True
        run(['createdb','-h','/work/socket','-p','55467','-U','baseline_owner','-T','template0','baseline'])
        run(['pg_restore','--no-owner','--no-acl','--exit-on-error','--single-transaction','-h','/work/socket','-p','55467','-U','baseline_owner','-d','baseline','/work/archive.dump'])
    finally:
        if started:run(['pg_ctl','-D','/work/data','-m','fast','-w','stop'])

def main():
    sys.path.insert(0,str(ROOT))
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(V)
    if validate_volatile_storage(minimum_bytes=9*GIB)!=V:raise ValueError('volume_or_capacity')
    receipt=json.loads((EXPORT/'export_receipt.json').read_text())
    st=SOURCE.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_size!=receipt['size_bytes'] or sha(SOURCE)!=receipt['sha256']:raise ValueError('dump_identity')
    if os.path.lexists(W):raise ValueError('destination_exists')
    before=free();os.umask(0o077);W.mkdir(mode=0o700)
    (W/'passwd').write_text(f'baseline:x:{os.getuid()}:{os.getgid()}:Baseline:/work:/bin/false\n')
    (W/'group').write_text(f'baseline:x:{os.getgid()}:\n')
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent',
         '--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--bind',str(W),'/work','--ro-bind',str(SOURCE),'/work/archive.dump',
         '--dir','/etc','--ro-bind',str(W/'passwd'),'/etc/passwd','--ro-bind',str(W/'group'),'/etc/group',
         '--ro-bind',str(ROOT),'/project','--clearenv','--setenv','HOME','/work','--setenv','PATH','/usr/bin:/bin',
         '--','/project/.venv/bin/python','-B','/project/scripts/restore_final_candidate_artifact.py','--worker']
    p=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,start_new_session=True)
    deadline=time.monotonic()+1800
    while p.poll() is None:
        if free()<6*GIB+256*1024**2 or before-free()>3*GIB:
            os.killpg(p.pid,signal.SIGKILL);p.wait();raise RuntimeError('space_guard')
        if time.monotonic()>deadline:
            os.killpg(p.pid,signal.SIGKILL);p.wait();raise RuntimeError('restore_timeout')
        time.sleep(.2)
    _,err=p.communicate()
    if p.returncode:
        print(err.decode(errors='replace')[-1000:],file=sys.stderr)
        raise RuntimeError('isolated_restore_failed_see_encrypted_log')
    if (W/'data/postmaster.pid').exists() or free()<6*GIB:raise ValueError('restore_incomplete')
    output={'status':'RESTORED_PENDING_COMPARISON','sha256':receipt['sha256'],'cluster':str(W),'database':'baseline','network':'disabled','space_free_bytes':free()}
    fd=os.open(EXPORT/'restore_receipt.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(output,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(output))

if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()

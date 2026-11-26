"""Export the attested final candidate into LUKS without modifying its cluster."""
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
V=ROOT/'auditoria_local/recovery_19nov_capacity'
W=V/'funk-404-674-independent-trial'
MARKER=V/'final_candidate_protected_reference_v2.json'
DEST=V/'final_candidate_export_20261126_v3'
GIB=1024**3
EXPECTED_MARKER='51c8a8d814ef147e98a8238f46d32c323c92d36a44a89015068939f9f2399e4d'


def sha(path):
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    with os.fdopen(fd,'rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()


def free():
    fs=os.statvfs(V);return fs.f_bavail*fs.f_frsize


def worker():
    pg='/usr/lib/postgresql/16/bin/'
    started=False
    try:
        subprocess.run([pg+'pg_ctl','-D','/work/data','-l','/tmp/pg.log','-w','start'],check=True,stdout=subprocess.DEVNULL)
        started=True
        sql="SELECT (SELECT count(*) FROM documents),(SELECT count(*) FROM award_results),(SELECT count(*) FROM award_assignments),(SELECT count(*) FROM docent_bag_members),(SELECT count(*) FROM difficult_coverage_positions),(SELECT count(*) FROM difficult_coverage_candidates)"
        result=subprocess.check_output([pg+'psql','-X','-w','-qAt','-v','ON_ERROR_STOP=1','-h','/work/socket','-p','55467','-U','baseline_owner','-d','baseline_404_674_trial','-c',sql],text=True)
        if result.strip()!='778|593161|48077|61144|367|34601':
            raise ValueError('candidate_counts_mismatch')
        subprocess.run([pg+'pg_dump','-w','-h','/work/socket','-p','55467','-U','baseline_owner','-d','baseline_404_674_trial','-Fc','--lock-wait-timeout=5s'],check=True)
    finally:
        if started:subprocess.run([pg+'pg_ctl','-D','/work/data','-m','fast','-w','stop'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)


def main():
    sys.path.insert(0,str(ROOT))
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(V)
    if validate_volatile_storage(minimum_bytes=9*GIB)!=V:raise ValueError('volume_or_capacity')
    if sha(MARKER)!=EXPECTED_MARKER:raise ValueError('candidate_marker_mismatch')
    marker=json.loads(MARKER.read_text())
    if marker.get('cluster')!=str(W) or marker.get('database')!='baseline_404_674_trial':raise ValueError('candidate_identity')
    for name,expected in marker['receipts'].items():
        if Path(name).name!=name or sha(V/name)!=expected:raise ValueError('candidate_evidence_mismatch')
    for p in (W,W/'data',W/'socket'):
        st=p.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:raise ValueError('candidate_permissions')
    if (W/'data/postmaster.pid').exists() or os.path.lexists(DEST):raise ValueError('cluster_running_or_destination_exists')
    available=free();DEST.mkdir(mode=0o700)
    tmp=DEST/'candidate.dump.partial'
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent',
         '--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
         '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/etc','--ro-bind',str(W/'passwd'),'/etc/passwd',
         '--ro-bind',str(W/'group'),'/etc/group','--overlay-src',str(W),'--tmp-overlay','/work','--ro-bind',str(ROOT),'/project',
         '--clearenv','--setenv','HOME','/tmp','--setenv','PATH','/usr/bin:/bin','--','/project/.venv/bin/python','-B',
         '/project/scripts/export_final_recovery_candidate.py','--worker']
    with os.fdopen(fd,'wb') as out:
        p=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.PIPE,start_new_session=True)
        deadline=time.monotonic()+1800
        while p.poll() is None:
            if free()<6*GIB+256*1024**2 or available-free()>3*GIB:
                os.killpg(p.pid,signal.SIGKILL);p.wait();raise RuntimeError('space_guard')
            if time.monotonic()>deadline:
                os.killpg(p.pid,signal.SIGKILL);p.wait();raise RuntimeError('export_timeout')
            time.sleep(.2)
        _,err=p.communicate();out.flush();os.fsync(out.fileno())
    if p.returncode:
        print(err.decode(errors='replace')[-1500:],file=sys.stderr)
        raise RuntimeError('isolated_export_failed')
    if free()<6*GIB:raise RuntimeError('space_margin')
    subprocess.run(['/usr/lib/postgresql/16/bin/pg_restore','--list',str(tmp)],stdout=subprocess.DEVNULL,check=True)
    digest=sha(tmp);size=tmp.stat().st_size
    if not size:raise ValueError('empty_dump')
    final=DEST/'candidate.dump';os.link(tmp,final,follow_symlinks=False);tmp.unlink()
    receipt={'status':'EXPORTED_TOC_VERIFIED','sha256':digest,'size_bytes':size,'candidate_marker_sha256':EXPECTED_MARKER,
             'original_cluster':str(W),'original_database':'baseline_404_674_trial','dump':str(final),'space_free_bytes':free()}
    fd=os.open(DEST/'export_receipt.json',os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(receipt,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps(receipt))


if __name__=='__main__':
    if '--worker' in sys.argv:worker()
    else:main()

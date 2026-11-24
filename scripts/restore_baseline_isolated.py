"""Restore an authenticated ciphertext into a private, volatile PG16 cluster."""
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile

from app.services.encrypted_baseline_copy import decrypt_verified

ROOT=Path(__file__).resolve().parents[1]
WORKER=ROOT/'scripts/restore_baseline_worker.py'
NORMALIZE=ROOT/'app/storage/migrations/20261106_normalize_text_restore.sql'

def validate_volatile_storage(minimum_bytes=4*1024**3):
    # Sensitive decrypted pages must not be written to unencrypted swap.
    for line in Path('/proc/swaps').read_text().splitlines()[1:]:
        device=line.split()[0];name=Path(device).name
        if name.startswith('dm-'):
            if not (Path('/sys/block')/name/'dm/uuid').read_text().strip().startswith('CRYPT-'):raise RuntimeError('swap_not_encrypted')
        elif name.startswith('zram'):
            if (Path('/sys/block')/name/'backing_dev').read_text().strip()!='none':raise RuntimeError('zram_backing_not_disabled')
        else:raise RuntimeError('swap_not_encrypted')
    configured=os.environ.get('FUNK_RECOVERY_WORK_ROOT','').strip()
    if configured:
        root=Path(configured)
        if not root.is_absolute() or root.is_symlink() or root.resolve()!=root:
            raise RuntimeError('encrypted_work_path_invalid')
        st=root.stat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o700:
            raise RuntimeError('encrypted_work_permissions_invalid')
        mount=subprocess.check_output(['/usr/bin/findmnt','-n','-o','OPTIONS','-T',str(root)],text=True).strip().split(',')
        if 'rw' not in mount:raise RuntimeError('encrypted_work_not_writable')
        uuid=Path('/sys/dev/block')/f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}'/'dm/uuid'
        try:identifier=uuid.read_text().strip()
        except OSError:raise RuntimeError('encrypted_work_not_luks') from None
        if not identifier.startswith('CRYPT-LUKS'):raise RuntimeError('encrypted_work_not_luks')
        v=os.statvfs(root)
        if v.f_bavail*v.f_frsize<minimum_bytes:raise RuntimeError('encrypted_work_space_insufficient')
        return root
    mount=subprocess.check_output(['/usr/bin/findmnt','-n','-o','FSTYPE','-T','/dev/shm'],text=True).strip()
    if mount!='tmpfs':raise RuntimeError('work_is_not_tmpfs')
    st=os.statvfs('/dev/shm')
    if st.f_bavail*st.f_frsize<minimum_bytes:raise RuntimeError('tmpfs_space_insufficient')
    return Path('/dev/shm')

def restore_ciphertext(ciphertext,key,digest,size):
    root=validate_volatile_storage()
    work=Path(tempfile.mkdtemp(prefix='funk-baseline-',dir=root));work.chmod(0o700)
    archive=work/'archive.dump';success=False
    try:
        plain=decrypt_verified(ciphertext,key,digest,size)
        fd=os.open(archive,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'wb') as stream:stream.write(plain)
        del plain
        (work/'passwd').write_text(f'baseline:x:{os.getuid()}:{os.getgid()}:Baseline:/work:/bin/false\n')
        (work/'group').write_text(f'baseline:x:{os.getgid()}:\n')
        cmd=['/usr/bin/bwrap','--unshare-user','--unshare-net','--unshare-pid','--die-with-parent',
             '--ro-bind','/usr','/usr','--symlink','usr/lib','/lib','--symlink','usr/lib64','/lib64','--symlink','usr/bin','/bin',
             '--proc','/proc','--dev','/dev','--bind',str(work),'/work','--dir','/code','--dir','/etc',
             '--ro-bind',str(work/'passwd'),'/etc/passwd','--ro-bind',str(work/'group'),'/etc/group',
             '--ro-bind',str(WORKER),'/code/worker.py','--ro-bind',str(NORMALIZE),'/code/normalize.sql',
             '--uid',str(os.getuid()),'--gid',str(os.getgid()),'--','/usr/bin/python3','-I','/code/worker.py']
        process=subprocess.run(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=3600)
        if process.returncode:
            try:
                failure=json.loads(process.stderr.decode())
                condition=failure.get('condition','')
                if condition and all(c.isascii() and (c.isalnum() or c=='_') for c in condition):
                    raise RuntimeError('isolated_restore_failed_'+condition)
            except (ValueError,UnicodeError):pass
            raise RuntimeError('isolated_restore_failed_launcher')
        result=json.loads((work/'result.json').read_text())
        if result.get('status')!='RESTORED' or not (work/'data/PG_VERSION').is_file():raise RuntimeError('restore_receipt_invalid')
        archive.unlink();success=True
        return work,result
    finally:
        archive.unlink(missing_ok=True)
        if not success:shutil.rmtree(work,ignore_errors=True)

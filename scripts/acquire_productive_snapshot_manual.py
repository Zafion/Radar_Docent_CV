"""Manually stream a separately authorised pg_dump into the verified LUKS volume.

Requires the pre-existing SSH key and passwordless sudo for this exact command.
Does not change sudo configuration. A failed precondition is a hard stop.
"""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
from datetime import datetime, timezone

ROOT = Path('/home/zafion/Proyects/Radar_Docent_CV/auditoria_local/recovery_19nov_capacity')
SSH = ['/usr/bin/ssh', '-T', '-F', '/dev/null', '-i', '/home/zafion/.ssh/funkcionario_prod']
for option in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'IdentitiesOnly=yes',
               'UserKnownHostsFile=/home/zafion/.ssh/known_hosts', 'IdentityAgent=none',
               'ForwardAgent=no', 'ClearAllForwardings=yes', 'ProxyCommand=none',
               'ProxyJump=none', 'ControlMaster=no', 'ControlPath=none',
               'ConnectTimeout=10', 'ConnectionAttempts=1', 'ServerAliveInterval=15',
               'ServerAliveCountMax=3'):
    SSH += ['-o', option]
SSH += ['funkcionario@178.104.237.94']
REMOTE = 'sudo -n -u postgres /usr/lib/postgresql/16/bin/pg_dump -w --format=custom --lock-wait-timeout=5s --dbname=radar_docent_cv'


def main():
    st = ROOT.stat()
    uuid = (Path('/sys/dev/block') / f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}' / 'dm/uuid').read_text().strip()
    opts = subprocess.check_output(['/usr/bin/findmnt', '-n', '-o', 'OPTIONS', '-T', str(ROOT)], text=True).strip().split(',')
    if ROOT.is_symlink() or ROOT.resolve() != ROOT or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o700 or not uuid.startswith('CRYPT-LUKS2-') or 'rw' not in opts:
        raise ValueError('unsafe_luks_root')
    if os.statvfs(ROOT).f_bavail * os.statvfs(ROOT).f_frsize < 2 * 1024**3:
        raise ValueError('less_than_2GiB_for_archive_and_margin')
    name = 'productive_snapshot_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = ROOT / name
    dest.mkdir(mode=0o700)
    partial = dest / 'snapshot.dump.partial'
    started = datetime.now(timezone.utc).isoformat()
    proc = None
    try:
        fd = os.open(partial, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        proc = subprocess.Popen(SSH + [REMOTE], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(fd, 'wb') as out:
            for chunk in iter(lambda: proc.stdout.read(1024 * 1024), b''):
                fs = os.statvfs(ROOT)
                if fs.f_bavail * fs.f_frsize < 1024**3 + len(chunk):
                    raise ValueError('encrypted_volume_margin_exhausted')
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        if proc.wait() or not size:
            raise ValueError('ssh_or_pg_dump_failed')
        reread = hashlib.sha256()
        with partial.open('rb') as stream:
            if stream.read(5) != b'PGDMP':
                raise ValueError('not_custom_archive')
            stream.seek(0)
            for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                reread.update(chunk)
        if partial.stat().st_size != size or digest.digest() != reread.digest():
            raise ValueError('stored_digest_mismatch')
        subprocess.run(['/usr/lib/postgresql/16/bin/pg_restore', '--list', str(partial)],
                       check=True, stdout=subprocess.DEVNULL, timeout=180)
        final = dest / 'snapshot.dump'
        os.link(partial, final, follow_symlinks=False)
        partial.unlink()
        receipt = {'status': 'VERIFIED_TOC', 'started_utc': started,
                   'finished_utc': datetime.now(timezone.utc).isoformat(),
                   'size_bytes': size, 'sha256': digest.hexdigest(),
                   'format': 'postgresql_custom', 'pg_restore_list': 'PASS',
                   'source': 'radar_docent_cv via SSH pg_dump',
                   'note': 'TOC readability and transfer integrity do not prove full restore or application identity.'}
        fd = os.open(dest / 'receipt.json', os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'w') as out:
            json.dump(receipt, out, indent=2)
            out.write('\n')
            out.flush()
            os.fsync(out.fileno())
        print(json.dumps({'directory': str(dest), **receipt}))
    except BaseException:
        if proc and proc.poll() is None:
            proc.terminate()
            proc.wait()
        partial.unlink(missing_ok=True)
        raise


if __name__ == '__main__':
    main()

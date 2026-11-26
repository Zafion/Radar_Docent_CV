"""Compare two stopped local LUKS clusters using isolated temporary overlays."""
import os
from pathlib import Path
import stat
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VOLUME = ROOT / 'auditoria_local/recovery_19nov_capacity'


def checked(path):
    p = Path(path)
    if p.is_symlink() or p.resolve() != p or p.parent != VOLUME:
        raise ValueError('cluster_path_invalid')
    for item in (p, p / 'data', p / 'socket'):
        s = item.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != os.getuid() or stat.S_IMODE(s.st_mode) != 0o700 or s.st_dev != VOLUME.stat().st_dev:
            raise ValueError('cluster_permissions_or_volume')
    if (p / 'data/postmaster.pid').exists() or (p / 'data/PG_VERSION').read_text().strip() != '16':
        raise ValueError('cluster_not_stopped_or_not_pg16')
    return p


def main():
    if len(sys.argv) != 3:
        raise SystemExit('usage: compare_productive_snapshot_local.py ORIGINAL_CLUSTER NEW_CLUSTER')
    s = VOLUME.stat()
    uuid = (Path('/sys/dev/block') / f'{os.major(s.st_dev)}:{os.minor(s.st_dev)}' / 'dm/uuid').read_text().strip()
    if not uuid.startswith('CRYPT-LUKS2-'):
        raise ValueError('volume_not_luks2')
    left, right = checked(sys.argv[1]), checked(sys.argv[2])
    if left == right:
        raise ValueError('clusters_must_differ')
    cmd = ['/usr/bin/bwrap', '--unshare-user', '--unshare-net', '--unshare-pid', '--die-with-parent',
           '--ro-bind', '/usr', '/usr', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64',
           '--symlink', 'usr/bin', '/bin', '--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/etc',
           '--ro-bind', str(left/'passwd'), '/etc/passwd', '--ro-bind', str(left/'group'), '/etc/group',
           '--overlay-src', str(left), '--tmp-overlay', '/left', '--overlay-src', str(right), '--tmp-overlay', '/right',
           '--ro-bind', str(ROOT), '/project', '--clearenv', '--setenv', 'HOME', '/tmp',
           '--setenv', 'PATH', '/usr/bin:/bin', '--setenv', 'PGSERVICEFILE', '/tmp/pg_service.conf',
           '--', '/bin/bash', '-c', r'''set -eu
printf '[old]\nhost=/left/socket\nport=55467\nuser=baseline_owner\ndbname=baseline\n[new]\nhost=/right/socket\nport=55467\nuser=baseline_owner\ndbname=baseline\n' > /tmp/pg_service.conf
pg=/usr/lib/postgresql/16/bin/pg_ctl
cleanup() { "$pg" -D /right/data -m fast -w stop >/dev/null 2>&1 || :; "$pg" -D /left/data -m fast -w stop >/dev/null 2>&1 || :; }
trap cleanup EXIT
"$pg" -D /left/data -o '-c unix_socket_directories=/left/socket' -l /tmp/left.log -w start >/dev/null
"$pg" -D /right/data -o '-c unix_socket_directories=/right/socket' -l /tmp/right.log -w start >/dev/null
/project/.venv/bin/python -B /project/scripts/compare_backup_contents.py --left-service old --right-service new
''']
    return subprocess.call(cmd)


if __name__ == '__main__':
    sys.exit(main())

"""Point-in-time Linux flock observation; not a concurrency fence."""
import os
from pathlib import Path
import stat


def verify_held_flocks(pid, start_ticks, paths):
    """Verify owner and path inode against /proc/locks; raise on ambiguity.

    Callers must still implement a fail-closed continuous fence around every
    write. This observation alone must not authorize an operational runner.
    """
    if type(pid) is not int or pid < 1 or not isinstance(start_ticks,str) or not start_ticks.isdecimal():
        raise ValueError('invalid_identity')
    if not isinstance(paths,(tuple,list)) or len(paths)!=2 or len(set(paths))!=2:
        raise ValueError('two_distinct_locks_required')
    proc=Path('/proc')/str(pid)
    def started():
        return (proc/'stat').read_text().rsplit(')',1)[1].split()[19]
    if started()!=start_ticks:
        raise ValueError('owner_identity_changed')
    locks=Path('/proc/locks').read_text().splitlines()
    for path in paths:
        info=os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:
            raise ValueError('lock_path_not_regular')
        identity=(os.major(info.st_dev),os.minor(info.st_dev),info.st_ino)
        matches=0
        for line in locks:
            fields=line.split()
            if len(fields)<6 or fields[1]!='FLOCK' or fields[3]!='WRITE' or fields[4]!=str(pid):
                continue
            try:
                major,minor,inode=fields[5].split(':')
                if (int(major,16),int(minor,16),int(inode))==identity:
                    matches+=1
            except ValueError:
                raise ValueError('invalid_lock_metadata') from None
        if matches!=1:
            raise ValueError('lock_not_verified')
        current=os.lstat(path)
        if (current.st_dev,current.st_ino,current.st_mode,current.st_nlink)!=(info.st_dev,info.st_ino,info.st_mode,info.st_nlink):
            raise ValueError('lock_path_changed')
    if started()!=start_ticks:
        raise ValueError('owner_identity_changed')

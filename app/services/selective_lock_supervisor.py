"""Local flock supervisor primitive; not an operational ingestion launcher.

The selected child must retain the inherited lock descriptors and must not
delegate writes to descendants that close them. External authorization,
provenance and a commit-time fence are deliberately outside this primitive.
"""
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import threading
import time
from app.services.selective_lock_observation import verify_held_flocks


class InheritedLockFence:
    """Verify inherited flock descriptions before database operations.

    A check and a PostgreSQL commit cannot be made atomic with flock. This
    fence supplements, but does not replace, the maintenance window.
    """
    def __init__(self, paths):
        if len(paths) != 2 or len(set(map(str, paths))) != 2:
            raise ValueError('two_distinct_locks_required')
        self.paths = tuple(map(Path, paths))
        self.fds = []
        for path in self.paths:
            target = os.lstat(path)
            if not stat.S_ISREG(target.st_mode) or target.st_nlink != 1 or target.st_uid != os.getuid():
                raise ValueError('unsafe_lock_file')
            matches = []
            for fd_path in Path('/proc/self/fd').iterdir():
                try:
                    fd = int(fd_path.name)
                    info = os.fstat(fd)
                    if (info.st_dev,info.st_ino)==(target.st_dev,target.st_ino):
                        matches.append(fd)
                except (OSError,ValueError):
                    continue
            if len(matches)!=1:
                raise ValueError('inherited_lock_missing_or_ambiguous')
            self.fds.append(matches[0])
        self.check()

    def check(self):
        for path,fd in zip(self.paths,self.fds):
            held=os.fstat(fd); current=os.lstat(path)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink!=1 or current.st_uid!=os.getuid()
                    or (held.st_dev,held.st_ino)!=(current.st_dev,current.st_ino)):
                raise RuntimeError('inherited_lock_identity_changed')
            expected=(os.major(held.st_dev),os.minor(held.st_dev),held.st_ino)
            lines=(Path('/proc/self/fdinfo')/str(fd)).read_text().splitlines()
            found=0
            for line in lines:
                parts=line.split()
                if len(parts)<8 or parts[0]!='lock:' or parts[2]!='FLOCK' or parts[4]!='WRITE':
                    continue
                try:
                    major,minor,inode=parts[6].split(':')
                    found += (int(major,16),int(minor,16),int(inode))==expected
                except ValueError:
                    raise RuntimeError('invalid_inherited_lock_metadata') from None
            if found!=1:
                raise RuntimeError('inherited_flock_not_held')


class LockSupervisor:
    def __init__(self, paths):
        if len(paths) != 2 or len(set(map(str, paths))) != 2:
            raise ValueError('two_distinct_locks_required')
        self.paths = tuple(map(Path, paths))
        self.fds = []
        self.child = None
        self.cycle_lock = threading.Lock()
        self.cycle_consumed = False
        self.stopped = False
        self.owner_pid = os.getpid()
        self.start_ticks = (Path('/proc') / str(self.owner_pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        try:
            for path in self.paths:
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC)
                self.fds.append(fd)
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                    raise ValueError('unsafe_lock_file')
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.check()
        except BaseException:
            for fd in self.fds:
                os.close(fd)
            self.fds.clear()
            raise

    def check(self):
        if self.stopped or len(self.fds) != 2:
            raise RuntimeError('supervisor_stopped')
        if os.getpid() != self.owner_pid:
            raise RuntimeError('supervisor_identity_changed')
        for path, fd in zip(self.paths, self.fds):
            held, current = os.fstat(fd), os.lstat(path)
            if (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1
                    or current.st_uid != os.getuid()
                    or (held.st_dev, held.st_ino) != (current.st_dev, current.st_ino)):
                raise RuntimeError('lock_identity_changed')
        verify_held_flocks(self.owner_pid, self.start_ticks, self.paths)

    def run_once(self, command, *, interval=0.05):
        """Run one cooperating child; retain locks after success or failure."""
        if not self.cycle_lock.acquire(blocking=False):
            raise RuntimeError('selective_cycle_already_running')
        try:
            self.check()
            if self.cycle_consumed:
                raise RuntimeError('selective_cycle_already_consumed')
            if not command or not os.path.isabs(command[0]):
                raise ValueError('absolute_executable_required')
            self.cycle_consumed = True
            self.child = subprocess.Popen(command, close_fds=True, pass_fds=tuple(self.fds))
            try:
                while self.child.poll() is None:
                    self.check()
                    time.sleep(interval)
                self.check()
                return self.child.returncode
            except BaseException:
                if self.child.poll() is None:
                    self.child.terminate()
                    try:
                        self.child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        # Never release the locks while a child may still write.
                        self.stopped = True
                        raise RuntimeError('child_still_running_locks_retained') from None
                raise
            finally:
                if self.child.poll() is not None:
                    self.child = None
        finally:
            self.cycle_lock.release()

    def close(self):
        if self.child is not None and self.child.poll() is None:
            raise RuntimeError('child_still_running_locks_retained')
        for fd in self.fds:
            os.close(fd)
        self.fds.clear()
        self.stopped = True

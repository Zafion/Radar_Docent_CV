import fcntl
import os
from pathlib import Path
import tempfile
import unittest

from app.services.selective_lock_observation import verify_held_flocks


class LockObservationTests(unittest.TestCase):
    def test_exact_owner_both_locks_and_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths=[Path(tmp)/'one',Path(tmp)/'two']
            with paths[0].open('w') as one, paths[1].open('w') as two:
                fcntl.flock(one,fcntl.LOCK_EX|fcntl.LOCK_NB)
                fcntl.flock(two,fcntl.LOCK_EX|fcntl.LOCK_NB)
                pid=os.getpid()
                ticks=(Path('/proc')/str(pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
                verify_held_flocks(pid,ticks,paths)
                with self.assertRaises(ValueError): verify_held_flocks(pid,str(int(ticks)+1),paths)
                fcntl.flock(two,fcntl.LOCK_UN)
                with self.assertRaisesRegex(ValueError,'lock_not_verified'):
                    verify_held_flocks(pid,ticks,paths)


if __name__ == '__main__':
    unittest.main()

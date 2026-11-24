import fcntl
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from app.services.selective_lock_supervisor import LockSupervisor


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.paths = [Path(self.tmp.name)/'one', Path(self.tmp.name)/'two']
        for path in self.paths: path.touch(mode=0o600)
        self.guard = LockSupervisor(self.paths)
        self.addCleanup(self.guard.close)

    def test_exclusive_child_and_locks_held_after_completion(self):
        outcome=[]
        thread=threading.Thread(target=lambda: outcome.append(self.guard.run_once([sys.executable,'-c','import time; time.sleep(0.4)'])))
        thread.start()
        for _ in range(100):
            if self.guard.child is not None: break
            time.sleep(.01)
        with self.assertRaisesRegex(RuntimeError,'already_running'):
            self.guard.run_once([sys.executable,'-c','pass'])
        with self.assertRaises(BlockingIOError): LockSupervisor(self.paths)
        thread.join(3)
        self.assertEqual(outcome,[0])
        self.guard.check()
        with self.assertRaisesRegex(RuntimeError,'already_consumed'):
            self.guard.run_once([sys.executable,'-c','pass'])

    def test_replaced_lock_stops_child_without_unlocking_other(self):
        errors=[]
        def run():
            try: self.guard.run_once([sys.executable,'-c','import time; time.sleep(3)'])
            except Exception as e: errors.append(type(e).__name__)
        thread=threading.Thread(target=run); thread.start()
        for _ in range(100):
            if self.guard.child is not None: break
            time.sleep(.01)
        self.paths[0].rename(self.paths[0].with_suffix('.old'))
        self.paths[0].touch(mode=0o600)
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors,['RuntimeError'])
        with self.paths[1].open('r+') as held:
            with self.assertRaises(BlockingIOError): fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)

    def test_interruption_stops_child_and_keeps_locks(self):
        original=self.guard.check
        with patch.object(self.guard,'check',side_effect=[None,KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                self.guard.run_once([sys.executable,'-c','import time; time.sleep(3)'])
        self.assertIsNone(self.guard.child)
        original()

    def test_child_unlock_detected(self):
        fcntl.flock(self.guard.fds[0],fcntl.LOCK_UN)
        with self.assertRaisesRegex(ValueError,'lock_not_verified'):
            self.guard.check()

    def test_recovery_after_normal_release(self):
        self.assertEqual(self.guard.run_once([sys.executable,'-c','pass']),0)
        self.guard.close()
        replacement=LockSupervisor(self.paths)
        try:
            replacement.check()
            self.assertEqual(replacement.run_once([sys.executable,'-c','pass']),0)
        finally:
            replacement.close()

    def test_locks_survive_abrupt_supervisor_death_until_child_exits(self):
        self.guard.close()
        ready=Path(self.tmp.name)/'ready'
        child="from pathlib import Path; import time; Path(%r).touch(); time.sleep(1.5)" % str(ready)
        program=("import sys; from app.services.selective_lock_supervisor import LockSupervisor; "
                 "s=LockSupervisor(sys.argv[1:3]); s.run_once([sys.executable,'-c',sys.argv[3]])")
        parent=subprocess.Popen([sys.executable,'-c',program,*map(str,self.paths),child],
                                stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(200):
                if ready.exists(): break
                if parent.poll() is not None: self.fail('supervisor_exited_before_ready')
                time.sleep(.01)
            self.assertTrue(ready.exists())
            parent.kill(); parent.wait(timeout=2)
            for path in self.paths:
                with path.open('r+') as handle:
                    with self.assertRaises(BlockingIOError): fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with self.paths[0].open('r+') as handle:
                for _ in range(300):
                    try:
                        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                        break
                    except BlockingIOError: time.sleep(.01)
                else: self.fail('child_did_not_release_lock')
        finally:
            if parent.poll() is None:
                parent.kill(); parent.wait(timeout=2)


if __name__ == '__main__':
    unittest.main()

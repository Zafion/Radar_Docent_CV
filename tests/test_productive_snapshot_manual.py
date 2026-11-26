import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import acquire_productive_snapshot_manual as acquisition


class FakeSSH:
    def __init__(self, returncode):
        self.stdout = io.BytesIO(b'PGDMPsynthetic archive')
        self.returncode = returncode

    def wait(self):
        return self.returncode

    def poll(self):
        return self.returncode


class SnapshotTests(unittest.TestCase):
    def run_case(self, returncode):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root.chmod(0o700)
            actual_read = Path.read_text

            def read(path, *args, **kwargs):
                if str(path).startswith('/sys/dev/block/'):
                    return 'CRYPT-LUKS2-test'
                return actual_read(path, *args, **kwargs)

            with patch.object(acquisition, 'ROOT', root), patch.object(Path, 'read_text', read), \
                 patch.object(acquisition.subprocess, 'check_output', return_value='rw'), \
                 patch.object(acquisition.subprocess, 'Popen', return_value=FakeSSH(returncode)), \
                 patch.object(acquisition.subprocess, 'run'), contextlib.redirect_stdout(io.StringIO()):
                if returncode:
                    with self.assertRaisesRegex(ValueError, 'ssh_or_pg_dump_failed'):
                        acquisition.main()
                    snapshots = list(root.iterdir())
                    self.assertEqual(len(snapshots), 1)
                    self.assertFalse((snapshots[0] / 'snapshot.dump').exists())
                    self.assertFalse((snapshots[0] / 'receipt.json').exists())
                else:
                    acquisition.main()
                    snapshots = list(root.iterdir())
                    self.assertEqual(len(snapshots), 1)
                    receipt = json.loads((snapshots[0] / 'receipt.json').read_text())
                    self.assertEqual(receipt['status'], 'VERIFIED_TOC')
                    self.assertEqual(os.stat(snapshots[0] / 'snapshot.dump').st_mode & 0o777, 0o600)

    def test_successful_stream(self):
        self.run_case(0)

    def test_failure_not_published(self):
        self.run_case(1)


if __name__ == '__main__':
    unittest.main()

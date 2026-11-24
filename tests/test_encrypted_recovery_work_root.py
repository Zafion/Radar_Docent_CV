import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.restore_baseline_isolated import validate_volatile_storage


class RecoveryWorkRootTests(unittest.TestCase):
    def test_verified_luks_root_and_space(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory,0o700)
            real_read=Path.read_text
            def read(path,*args,**kwargs):
                if str(path).startswith('/sys/dev/block/') and str(path).endswith('/dm/uuid'):
                    return 'CRYPT-LUKS2-synthetic\n'
                return real_read(path,*args,**kwargs)
            with patch.dict(os.environ,{'FUNK_RECOVERY_WORK_ROOT':directory}),patch.object(Path,'read_text',read),patch('scripts.restore_baseline_isolated.subprocess.check_output',return_value='rw,nodev,nosuid\n'):
                self.assertEqual(validate_volatile_storage(minimum_bytes=0),Path(directory))
                with self.assertRaisesRegex(RuntimeError,'encrypted_work_space_insufficient'):
                    validate_volatile_storage(minimum_bytes=10**30)

    def test_plain_filesystem_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            os.chmod(directory,0o700)
            with patch.dict(os.environ,{'FUNK_RECOVERY_WORK_ROOT':directory}):
                with self.assertRaisesRegex(RuntimeError,'encrypted_work_not_luks'):
                    validate_volatile_storage(minimum_bytes=0)

    def test_loose_permissions_and_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)/'work';root.mkdir(mode=0o755);root.chmod(0o755)
            with patch.dict(os.environ,{'FUNK_RECOVERY_WORK_ROOT':str(root)}):
                with self.assertRaisesRegex(RuntimeError,'encrypted_work_permissions_invalid'):
                    validate_volatile_storage(minimum_bytes=0)
            link=Path(directory)/'link';link.symlink_to(root,target_is_directory=True)
            with patch.dict(os.environ,{'FUNK_RECOVERY_WORK_ROOT':str(link)}):
                with self.assertRaisesRegex(RuntimeError,'encrypted_work_path_invalid'):
                    validate_volatile_storage(minimum_bytes=0)

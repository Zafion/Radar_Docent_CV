"""Local synthetic checks for configurable restore paths; no server is started."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import restore_21nov_local as restore


class RestorePathTests(unittest.TestCase):
    def test_explicit_paths_and_no_receipt_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);volume=root/'encrypted';volume.mkdir(mode=0o700)
            originals=root/'source';originals.mkdir();dump=originals/'baseline.dump';dump.write_bytes(b'fixture');dump.chmod(0o600)
            (originals/'receipt.json').write_text(json.dumps({'status':'VERIFIED','local_sha256':'fixture-sha','size':7}))
            def encrypt(stream,path,digest,size):
                self.assertEqual(stream.read(),b'fixture');path.write_bytes(b'encrypted');return {'key':b'key'}
            cluster=volume/'new-cluster'
            def launch(path,key,digest,size):
                self.assertEqual(path.parent,volume);cluster.mkdir();return cluster,{'status':'RESTORED'}
            with patch.object(restore,'SHA','fixture-sha'),patch.object(restore,'SIZE',7), \
                 patch.object(restore,'validate_volatile_storage',return_value=volume), \
                 patch.object(restore,'encrypt_copy',side_effect=encrypt),patch.object(restore,'restore_ciphertext',side_effect=launch):
                restore.run(volume,dump)
                self.assertEqual(json.loads((volume/'baseline_21nov.restore.json').read_text())['work_directory'],str(cluster))
                with self.assertRaisesRegex(ValueError,'restore_receipt_already_exists'):restore.run(volume,dump)
                self.assertEqual(dump.read_bytes(),b'fixture')

    def test_rejects_receipt_outside_volume(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);volume=root/'encrypted';volume.mkdir(mode=0o700)
            with patch.object(restore,'validate_volatile_storage',return_value=volume):
                with self.assertRaisesRegex(ValueError,'receipt_directory_invalid'):
                    restore.run(volume,root/'missing',root)

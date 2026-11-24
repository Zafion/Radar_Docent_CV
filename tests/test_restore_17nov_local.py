import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from scripts import restore_17nov_local as restore

class RestoreSeventeenTests(unittest.TestCase):
    def test_verified_archive_uses_existing_isolated_worker(self):
        data=b'local synthetic archive'
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'baseline_17nov.dump';source.write_bytes(data);source.chmod(0o600)
            sha=hashlib.sha256(data).hexdigest()
            (root/'baseline_17nov.receipt.json').write_text(json.dumps({'status':'VERIFIED','sha256':sha,'size':len(data)}))
            with patch.object(restore,'SHA',sha),patch.object(restore,'SIZE',len(data)),patch.object(restore,'validate_volatile_storage',return_value=root),patch.object(restore,'restore_ciphertext',return_value=(root/'funk-baseline-test',{'status':'RESTORED'})) as worker:
                restore.run()
                self.assertTrue(worker.called)
                self.assertEqual(source.read_bytes(),data)
                self.assertEqual(json.loads((root/'baseline_17nov.restore.json').read_text())['source_sha256'],sha)
                self.assertFalse(list(root.glob('.restore_cipher_*')))
                with self.assertRaisesRegex(ValueError,'restore_receipt_already_exists'):restore.run()

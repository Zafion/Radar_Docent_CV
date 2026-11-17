import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.services.selective_manifest import ManifestRejected, validate_technical_manifest


class TechnicalManifestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = b'synthetic document'
        (self.root / 'doc.pdf').write_bytes(self.payload)
        self.data = {'format': 'funkcionario.selective-technical.v1', 'entries': [{
            'identity': 'synthetic-1', 'file': 'doc.pdf',
            'sha256': hashlib.sha256(self.payload).hexdigest(), 'size_bytes': len(self.payload)}]}

    def tearDown(self):
        self.tmp.cleanup()

    def validate(self):
        raw = json.dumps(self.data).encode()
        return validate_technical_manifest(self.root, raw, hashlib.sha256(raw).hexdigest())

    def test_valid(self):
        self.assertEqual(self.validate()[0]['identity'], 'synthetic-1')

    def test_changed_manifest_and_file_rejected(self):
        raw = json.dumps(self.data).encode()
        with self.assertRaises(ManifestRejected):
            validate_technical_manifest(self.root, raw, '0' * 64)
        (self.root / 'doc.pdf').write_bytes(b'wrong document')
        with self.assertRaises(ManifestRejected):
            self.validate()

    def test_extra_missing_and_symlink_rejected(self):
        (self.root / 'extra').write_bytes(b'x')
        with self.assertRaises(ManifestRejected): self.validate()
        (self.root / 'extra').unlink()
        (self.root / 'doc.pdf').unlink()
        with self.assertRaises(ManifestRejected): self.validate()
        (self.root / 'doc.pdf').symlink_to('/dev/null')
        with self.assertRaises(ManifestRejected): self.validate()


if __name__ == '__main__':
    unittest.main()

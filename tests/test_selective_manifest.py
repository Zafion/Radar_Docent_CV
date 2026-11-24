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

    def test_v3_requires_capture_hash_and_exact_document_link(self):
        page=b'<a href="/doc.pdf">document</a>'
        (self.root/'page.html').write_bytes(page)
        self.data['format']='funkcionario.selective-technical.v3'
        self.data['entries'][0]['asset']={'source_key':'synthetic','source_url':'https://example.invalid/page',
            'canonical_url':'https://example.invalid/doc.pdf','url':'https://example.invalid/doc.pdf',
            'asset_role':'pdf_candidate','title':'synthetic','publication_date_text':'not verified'}
        self.data['entries'][0]['evidence']={'html_file':'page.html','html_sha256':hashlib.sha256(page).hexdigest(),
            'final_page_url':'https://example.invalid/page','linked_url':'https://example.invalid/doc.pdf'}
        self.assertEqual(len(self.validate()),1)
        altered=b'<a href="/other.pdf">changed</a>'
        (self.root/'page.html').write_bytes(altered)
        with self.assertRaises(ManifestRejected): self.validate()
        self.data['entries'][0]['evidence']['html_sha256']=hashlib.sha256(altered).hexdigest()
        with self.assertRaises(ManifestRejected): self.validate()


if __name__ == '__main__':
    unittest.main()

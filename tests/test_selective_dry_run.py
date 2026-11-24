from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from app.services.selective_dry_run import dry_run


class DryRunTests(unittest.TestCase):
    def test_candidate_is_not_authorized_and_inputs_are_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            staging=Path(tmp); payload=b'synthetic'; (staging/'doc.pdf').write_bytes(payload)
            url='https://example.invalid/doc.pdf'
            raw=json.dumps({'format':'funkcionario.selective-technical.v2','entries':[{
                'identity':url,'file':'doc.pdf','sha256':hashlib.sha256(payload).hexdigest(),'size_bytes':len(payload),
                'asset':{'source_key':'synthetic','source_url':'https://example.invalid','canonical_url':url,'url':url,
                         'asset_role':'pdf_candidate','title':'synthetic','publication_date_text':'18/11/2026'}}]}).encode()
            inventory=json.dumps({'format':'funkcionario.inventory-screen.v1','cutover_utc':'2026-11-17T10:00:00Z',
                                  'observed_utc':'2026-11-18T10:00:00Z','versions':[]}).encode()
            fence=Mock()
            result=dry_run(staging=staging,manifest=raw,manifest_sha256=hashlib.sha256(raw).hexdigest(),
                           inventory_bytes=inventory,inventory_sha256=hashlib.sha256(inventory).hexdigest(),fence=fence)
            self.assertEqual(result['status'],'STOP')
            self.assertEqual(result['counts'],{'candidate_requires_official_provenance':1})
            self.assertFalse(result['writes_performed']); self.assertEqual(fence.check.call_count,2)
            self.assertEqual((staging/'doc.pdf').read_bytes(),payload)
            with self.assertRaisesRegex(ValueError,'inventory_digest'):
                dry_run(staging=staging,manifest=raw,manifest_sha256=hashlib.sha256(raw).hexdigest(),
                        inventory_bytes=inventory,inventory_sha256='0'*64,fence=fence)


if __name__=='__main__':
    unittest.main()

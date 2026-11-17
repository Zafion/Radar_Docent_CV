"""Ingestion intent is captured before classification; HTTP always simulated."""
import contextlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
import httpx
from app.services.document_sync import DocumentSyncService
from app.services.discovery.base import DiscoveredAsset
from tests.document_fixtures import pdf_bytes


class IngestionIntentTests(unittest.TestCase):
    def test_invalid_processing_mode_fails_before_http(self):
        with tempfile.TemporaryDirectory(dir='docs/auditoria_evidencias') as tmp:
            service=DocumentSyncService(base_dir=tmp)
            with patch.dict('os.environ',{'NOTIFICATION_PROCESSING_INTENT':'invalid'}),patch('app.services.document_sync.httpx.stream') as request:
                with self.assertRaisesRegex(ValueError,'invalid_notification_processing_intent'):
                    service._download_and_store_asset(object(),Path(tmp),Mock())
            request.assert_not_called()

    def test_download_records_reprocess_at_first_ingestion(self):
        url='https://example.invalid/synthetic.pdf'
        asset=DiscoveredAsset('synthetic','https://example.invalid',None,None,'pdf_candidate','synthetic',url,url,None,True)
        response=httpx.Response(200,content=pdf_bytes('synthetic'),headers={'content-type':'application/pdf'},request=httpx.Request('GET',url))
        store=Mock();store.get_document_version_by_sha256.return_value=None;store.create_document_version.return_value=1
        with tempfile.TemporaryDirectory(dir='docs/auditoria_evidencias') as tmp:
            service=DocumentSyncService(base_dir=tmp)
            with patch.dict('os.environ',{'NOTIFICATION_PROCESSING_INTENT':'reprocess'}),patch('app.services.document_sync.httpx.stream',return_value=contextlib.nullcontext(response)):
                service._download_and_store_asset(asset,Path(tmp),store)
        self.assertEqual(store.create_document_version.call_args.kwargs['notification_intent'],'reprocess')

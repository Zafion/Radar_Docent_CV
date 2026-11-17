"""Validación de descarga con respuestas HTTP simuladas y archivos temporales."""
import hashlib
from pathlib import Path
import tempfile
import unittest
import httpx
from scripts import fetch_ceice_remote as fetcher
from tests.document_fixtures import pdf_bytes,docx_bytes

ROOT=Path(__file__).resolve().parents[1]
URL='https://example.invalid/documents/1/2/sample.pdf'


class DocumentPayloadValidationTests(unittest.TestCase):
    def download(self,body,*,url=URL,headers=None,limit=None):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=body,headers=headers))) as client:
                kwargs={} if limit is None else {'max_download_bytes':limit}
                result=fetcher.download_asset(client=client,url=url,canonical_url=url,cache_files_dir=Path(tmp),**kwargs)
            return result

    def test_html_success_response_is_not_a_pdf(self):
        with self.assertRaises(ValueError):self.download(b'<html>Error</html>',headers={'content-type':'application/pdf'})

    def test_incomplete_pdf_is_rejected(self):
        for body in (b'%PDF-1.4\ntruncated',b'%PDF-1.4\ninvalid\n%%EOF'):
            with self.subTest(body=body),self.assertRaises(ValueError):self.download(body)

    def test_valid_pdf_uses_actual_format(self):
        result=self.download(pdf_bytes(),headers={'content-type':'text/plain'})
        self.assertEqual(result['content_type'],'application/pdf')
        self.assertTrue(result['stored_filename'].endswith('.pdf'))

    def test_docx_uuid_url_is_not_renamed_as_pdf(self):
        result=self.download(docx_bytes(),url='https://example.invalid/documents/1/2/ESPECIALISTAS.docx/opaque-uuid')
        self.assertEqual(result['original_filename'],'ESPECIALISTAS.docx')
        self.assertTrue(result['stored_filename'].endswith('.docx'))
        self.assertIn('wordprocessingml',result['content_type'])

    def test_docx_content_overrides_wrong_pdf_extension(self):
        self.assertTrue(self.download(docx_bytes())['stored_filename'].endswith('.docx'))

    def test_content_disposition_filename_is_sanitized(self):
        result=self.download(pdf_bytes(),headers={'content-disposition':'attachment; filename="../../sample.pdf"'})
        self.assertEqual(result['original_filename'],'sample.pdf')

    def test_download_size_limit_and_inconsistent_length(self):
        with self.assertRaises(ValueError):self.download(pdf_bytes(),headers={'content-length':'999999'},limit=1024)
        with self.assertRaises(ValueError):self.download(pdf_bytes(),limit=100)
        with self.assertRaises(ValueError):self.download(pdf_bytes(),headers={'content-length':'999999'})

    def test_corrupt_cache_is_not_reused_on_conditional_http(self):
        body=pdf_bytes();sha=hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cache=Path(tmp);name=fetcher.build_versioned_filename('sample.pdf',sha)
            (cache/name).write_bytes(b'%PDF-broken')
            previous={'sha256':sha,'stored_filename':name,'original_filename':'sample.pdf','etag':'"old"'}
            seen=[]
            def handler(request):
                seen.append(request)
                return httpx.Response(200,content=body)
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=previous)
            self.assertNotIn('if-none-match',seen[0].headers)
            self.assertEqual(Path(result['cache_file_path']).read_bytes(),body)

    def test_truncated_docx_is_rejected(self):
        with self.assertRaises(ValueError):self.download(docx_bytes()[:-20])

    def test_chunked_body_without_length_is_still_limited(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            def handler(request):
                return httpx.Response(200,stream=httpx.ByteStream(pdf_bytes()))
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaisesRegex(ValueError,'download_size_limit_exceeded'):
                    fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=Path(tmp),max_download_bytes=100)
            self.assertEqual(list(Path(tmp).iterdir()),[])

    def test_public_pdf_with_owner_permissions_is_still_readable(self):
        result=self.download(pdf_bytes(user_password=''))
        self.assertEqual(result['content_type'],'application/pdf')

    def test_pdf_requiring_a_reading_password_is_rejected(self):
        with self.assertRaises(ValueError):
            self.download(pdf_bytes(user_password='synthetic-required'))

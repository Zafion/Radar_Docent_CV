"""TTL y HTTP condicional sin red, con cachés temporales dentro del repositorio."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from datetime import timedelta
import httpx
from scripts import fetch_ceice_remote as fetcher
from app.services.discovery.base import DiscoveredAsset
from tests.document_fixtures import pdf_bytes

ROOT=Path(__file__).resolve().parents[1]
URL='https://example.invalid/documents/sample.pdf'


class FetcherRevalidationTests(unittest.TestCase):
    def test_legacy_state_without_timestamps_is_due(self):
        self.assertTrue(fetcher.revalidation_due({},timedelta(hours=24)))
        self.assertFalse(fetcher.revalidation_due({'last_checked_at':fetcher.utc_now_iso()},timedelta(hours=24)))

    def test_304_preserves_download_time_and_old_bytes(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cache=Path(tmp);(cache/'old.pdf').write_bytes(pdf_bytes('old'))
            old={'sha256':hashlib.sha256(pdf_bytes('old')).hexdigest(),'original_filename':'sample.pdf','stored_filename':'old.pdf',
                 'downloaded_at':'2026-01-01T00:00:00+00:00','etag':'"old"','last_modified':'Wed, 01 Jan 2026 00:00:00 GMT'}
            seen=[]
            def handler(request):
                seen.append(request)
                return httpx.Response(304)
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=old)
            self.assertEqual(result['downloaded_at'],old['downloaded_at'])
            self.assertNotEqual(result['last_checked_at'],old['downloaded_at'])
            self.assertEqual(seen[0].headers['if-none-match'],'"old"')
            self.assertEqual(seen[0].headers['if-modified-since'],old['last_modified'])
            self.assertEqual((cache/'old.pdf').read_bytes(),pdf_bytes('old'))

    def test_changed_hash_keeps_previous_version_and_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cache=Path(tmp);(cache/'old.pdf').write_bytes(pdf_bytes('old'))
            old={'sha256':hashlib.sha256(pdf_bytes('old')).hexdigest(),'original_filename':'sample.pdf',
                 'stored_filename':'old.pdf','downloaded_at':'2026-01-01T00:00:00+00:00'}
            with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=pdf_bytes('new'),headers={'etag':'"new"'}))) as client:
                result=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=old)
            self.assertNotEqual(result['sha256'],old['sha256'])
            self.assertEqual(result['previous_versions'][0]['sha256'],old['sha256'])
            self.assertTrue((cache/'old.pdf').exists())
            self.assertTrue(Path(result['cache_file_path']).exists())

    def run_fetch(self, urls, *, error=False, limit=1):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output=Path(tmp);cache=output/'cache/files';cache.mkdir(parents=True)
            (cache/'old.pdf').write_bytes(pdf_bytes('old'))
            state={'schema_version':1,'assets_by_canonical_url':{url:{'stored_filename':'old.pdf',
                'original_filename':'sample.pdf','sha256':hashlib.sha256(pdf_bytes('old')).hexdigest(),'downloaded_at':'2026-01-01T00:00:00+00:00'} for url in urls}}
            (output/'state.json').write_text(json.dumps(state))
            assets=[DiscoveredAsset('synthetic',URL,None,None,'pdf_candidate','Synthetic',url,url,None,True) for url in urls]
            adapter=Mock(source_key='synthetic',source_url=URL,source_label='Synthetic')
            adapter.discover_assets.return_value=assets
            requests=[]
            def handler(request):
                requests.append(request)
                return httpx.Response(500 if error else 304)
            client=httpx.Client(transport=httpx.MockTransport(handler))
            with patch.object(fetcher,'select_adapters',return_value=[adapter]),patch.object(fetcher.httpx,'Client',return_value=client),patch('sys.argv',['fetcher','--output-dir',tmp,'--download-delay','0','--max-revalidations',str(limit)]),contextlib.redirect_stdout(io.StringIO()):
                status=fetcher.main()
            state=json.loads((output/'state.json').read_text())
            manifest=json.loads((output/'current/manifest.json').read_text())
            linked=[(output/'current'/r['relative_file_path']).exists() for r in manifest['sources'][0]['results'] if r.get('relative_file_path')]
            return status,state,manifest,requests,linked

    def test_revalidation_budget_limits_legacy_cache_requests(self):
        status,state,manifest,requests,linked=self.run_fetch([URL+'?id=1',URL+'?id=2'])
        self.assertEqual(status,0)
        self.assertEqual(len(requests),1)
        self.assertEqual(linked,[True,True])
        self.assertIn('last_checked_at',state['assets_by_canonical_url'][URL+'?id=1'])
        self.assertNotIn('last_checked_at',state['assets_by_canonical_url'][URL+'?id=2'])

    def test_failed_check_preserves_cached_version_and_marks_partial(self):
        status,state,manifest,requests,linked=self.run_fetch([URL],error=True)
        self.assertEqual(status,1)
        self.assertEqual(state['assets_by_canonical_url'][URL]['sha256'],hashlib.sha256(pdf_bytes('old')).hexdigest())
        self.assertEqual(manifest['sources'][0]['status'],'partial')
        self.assertTrue(manifest['sources'][0]['results'][0]['revalidation_error'])
        self.assertEqual(linked,[True])

    def test_200_same_hash_does_not_add_false_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            data=pdf_bytes('same');cache=Path(tmp)
            old={'sha256':hashlib.sha256(data).hexdigest(),'stored_filename':'old.pdf',
                 'original_filename':'sample.pdf','downloaded_at':'2026-01-01T00:00:00+00:00'}
            (cache/'old.pdf').write_bytes(data)
            with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=data))) as client:
                result=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=old)
            self.assertEqual(result['sha256'],old['sha256'])
            self.assertEqual(result['previous_versions'],[])
            self.assertNotEqual(result['downloaded_at'],old['downloaded_at'])

    def test_forced_download_has_no_conditionals_but_keeps_history(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cache=Path(tmp);(cache/'old.pdf').write_bytes(pdf_bytes('old'))
            old={'sha256':hashlib.sha256(pdf_bytes('old')).hexdigest(),'stored_filename':'old.pdf','original_filename':'sample.pdf','etag':'"old"'}
            requests=[]
            def handler(request):
                requests.append(request)
                return httpx.Response(200,content=pdf_bytes('new'))
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=old,use_conditional_headers=False)
            self.assertNotIn('if-none-match',requests[0].headers)
            self.assertEqual(result['previous_versions'][0]['sha256'],hashlib.sha256(pdf_bytes('old')).hexdigest())

    def test_importer_preserves_partial_source(self):
        from scripts import import_remote_fetch as importer
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path=Path(tmp)/'manifest.json'
            path.write_text(json.dumps({'schema_version':1,'sources':[{
                'source_key':'synthetic','source_url':URL,'status':'partial','error_message':'simulated error',
                'assets':[],'results':[]}]}))
            store=Mock()
            with patch.object(importer,'SyncStore',return_value=store),contextlib.redirect_stdout(io.StringIO()):
                importer.import_manifest(manifest_path=path,incoming_dir=Path(tmp),data_dir=Path(tmp))
            self.assertEqual(store.finish_sync_run.call_args.kwargs['status'],'partial')
            self.assertEqual(store.finish_sync_run.call_args.kwargs['error_message'],'simulated error')

    def test_hash_changes_record_return_to_an_older_version(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            cache=Path(tmp);original=pdf_bytes('old');changed=pdf_bytes('new')
            (cache/'old.pdf').write_bytes(original)
            previous={'sha256':hashlib.sha256(original).hexdigest(),'stored_filename':'old.pdf','original_filename':'sample.pdf'}
            bodies=iter((changed,original))
            with httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=next(bodies)))) as client:
                first=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=previous)
                restored=fetcher.download_asset(client=client,url=URL,canonical_url=URL,cache_files_dir=cache,previous=first)
            self.assertEqual(restored['sha256'],previous['sha256'])
            self.assertEqual(len(restored['hash_changes']),2)
            self.assertEqual(restored['hash_changes'][1]['from_sha256'],first['sha256'])
            self.assertEqual(restored['hash_changes'][1]['to_sha256'],previous['sha256'])

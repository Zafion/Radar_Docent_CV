"""Descubrimiento completo/parcial/fallido y preservación de snapshots."""
import contextlib
import io
import json
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import httpx
from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter
from app.services.discovery.base import DiscoveredAsset
from app.services.discovery.generic_family import GenericFamilyDiscoveryAdapter
from app.services.document_sync import DocumentSyncService
from scripts import fetch_ceice_remote as fetcher
from tests.document_fixtures import pdf_bytes

ROOT=Path(__file__).resolve().parents[1]
URL='https://ceice.gva.es/es/web/rrhh-educacion'
DETAIL=URL+'/-/vacantes'
PDF='https://ceice.gva.es/documents/1/2/vacantes.pdf'

def reply(url,text='',status=200):
    return httpx.Response(status,text=text,headers={'content-type':'text/html','retry-after':'3600'},request=httpx.Request('GET',url))


class DiscoveryFailureStateTests(unittest.TestCase):
    def setUp(self):
        # Existing adapter tests simulate HTTP statuses; the separate loopback
        # integration test exercises PolicyClient's actual redirect policy.
        @contextlib.contextmanager
        def simulated_stream(client,method,url,**kwargs):
            yield httpx.get(url)
        patcher=patch('app.services.discovery.http_policy.PolicyClient.stream',simulated_stream)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_http_failures_preserve_status_and_retry_context(self):
        for status in (403,404,429,500):
            adapter=RrhhMainDiscoveryAdapter()
            with self.subTest(status=status),patch('app.services.discovery.base.httpx.get',return_value=reply(URL,status=status)) as get:
                self.assertEqual(adapter.discover_assets(),[])
                self.assertEqual(adapter.discovery_errors[0]['status_code'],status)
                self.assertEqual(adapter.discovery_errors[0]['url'],URL)
                self.assertEqual(get.call_count,1)

    def test_timeout_is_recorded_as_failure_not_missing_publications(self):
        adapter=RrhhMainDiscoveryAdapter()
        with patch('app.services.discovery.base.httpx.get',side_effect=httpx.ConnectTimeout('simulated')):
            self.assertEqual(adapter.discover_assets(),[])
        self.assertEqual(adapter.discovery_errors[0]['kind'],'timeout')

    def remote_status(self, pages):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output=Path(tmp);cache=output/'cache/files';cache.mkdir(parents=True)
            (cache/'old.pdf').write_bytes(pdf_bytes('old'))
            (output/'state.json').write_text(json.dumps({'assets_by_canonical_url':{PDF:{
                'sha256':hashlib.sha256(pdf_bytes('old')).hexdigest(),'stored_filename':'old.pdf','original_filename':'old.pdf','last_checked_at':fetcher.utc_now_iso()}}}))
            adapter=RrhhMainDiscoveryAdapter()
            with patch.object(fetcher,'select_adapters',return_value=[adapter]),patch('app.services.discovery.base.httpx.get',side_effect=lambda url,**kw:pages[url]),patch('sys.argv',['fetcher','--output-dir',tmp]),contextlib.redirect_stdout(io.StringIO()):
                code=fetcher.main()
            manifest=json.loads((output/'current/manifest.json').read_text())
            state=json.loads((output/'state.json').read_text())
            return code,manifest['sources'][0],state

    def test_root_http_failure_marks_source_failed_and_keeps_cache(self):
        code,source,state=self.remote_status({URL:reply(URL,status=403)})
        self.assertEqual(code,1)
        self.assertEqual(source['status'],'failed')
        self.assertEqual(state['assets_by_canonical_url'][PDF]['sha256'],hashlib.sha256(pdf_bytes('old')).hexdigest())

    def test_intermediate_failure_is_partial_with_valid_asset(self):
        code,source,_=self.remote_status({URL:reply(URL,f'<a href="{PDF}">Listado de vacantes</a><a href="{DETAIL}">Detalle</a>'),DETAIL:reply(DETAIL,status=503)})
        self.assertEqual(code,1)
        self.assertEqual(source['status'],'partial')
        self.assertEqual(len(source['assets']),1)
        self.assertEqual(source['discovery_errors'][0]['status_code'],503)

    def test_successful_empty_page_is_complete(self):
        code,source,_=self.remote_status({URL:reply(URL,'<h1>Sin publicaciones</h1>')})
        self.assertEqual(code,0)
        self.assertEqual(source['status'],'success')

    def test_optional_numeric_probe_404_is_not_connection_failure(self):
        adapter=GenericFamilyDiscoveryAdapter(source_key='synthetic',base_slug='bolsas',source_label='Synthetic',max_suffix=1,max_depth=0)
        with patch('app.services.discovery.base.httpx.get',side_effect=lambda url,**kw:reply(url,status=404 if url.endswith('1') else 200)):
            adapter.discover_assets()
        self.assertTrue(adapter.discovery_errors[0]['optional_probe'])
        self.assertEqual(adapter.discovery_errors[0]['status_code'],404)

    def test_direct_sync_failure_does_not_replace_last_complete_snapshot(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            source=Path(tmp)/'synthetic';source.mkdir()
            snapshot=source/'discovered_assets.json';snapshot.write_text('["previous-complete"]')
            adapter=SimpleNamespace(source_key='synthetic',source_url=URL,source_label='Synthetic',
                discovery_errors=[{'url':URL,'kind':'http_error','status_code':503}],discover_assets=lambda:[])
            store=Mock()
            with patch('app.services.document_sync.SyncStore',return_value=store):
                result=DocumentSyncService(tmp).sync_adapter(adapter)
            self.assertEqual(result['status'],'failed')
            self.assertEqual(json.loads(snapshot.read_text()),['previous-complete'])
            self.assertEqual(store.finish_sync_run.call_args.kwargs['status'],'failed')

    def test_direct_individual_download_error_is_partial_with_context(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            asset=DiscoveredAsset('synthetic',URL,None,None,'pdf_candidate','Listado de vacantes',PDF,PDF,None,True)
            adapter=SimpleNamespace(source_key='synthetic',source_url=URL,source_label='Synthetic',
                                    discovery_errors=[],discover_assets=lambda:[asset])
            with patch('app.services.document_sync.SyncStore',return_value=Mock()),patch.object(
                DocumentSyncService,'_download_and_store_asset',side_effect=httpx.ConnectTimeout('simulated')):
                result=DocumentSyncService(tmp).sync_adapter(adapter)
            self.assertEqual(result['status'],'partial')
            self.assertEqual(result['download_error_count'],1)
            report=json.loads((Path(tmp)/'synthetic/sync_report.json').read_text())
            self.assertEqual(report[0]['url'],PDF)
            self.assertEqual(report[0]['status'],'download_error')

    def test_sync_entrypoint_stops_downstream_pipeline_after_partial_run(self):
        import runpy
        summary=dict(source_key='synthetic',source_url=URL,started_at='synthetic',finished_at='synthetic',
            discovered_assets_count=1,downloadable_assets_count=1,new_versions_count=0,known_versions_count=0,
            same_run_duplicate_count=0,non_downloadable_count=0,download_error_count=1,source_dir='synthetic',status='partial')
        service=Mock();service.sync_adapter.return_value=summary
        with patch('app.services.document_sync.DocumentSyncService',return_value=service),patch(
            'app.services.discovery.registry.get_discovery_adapters',return_value=[SimpleNamespace(source_key='synthetic',source_url=URL)]),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as error:
                runpy.run_path('run_sync_all.py',run_name='__main__')
        self.assertEqual(error.exception.code,1)

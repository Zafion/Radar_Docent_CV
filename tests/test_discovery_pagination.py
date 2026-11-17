"""Offline HTML pagination regressions; preserve existing document cache keys."""
import unittest
from unittest.mock import patch
import httpx
from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter

ROOT = 'https://ceice.gva.es/es/web/rrhh-educacion'
PDF_ONE = 'https://ceice.gva.es/documents/1/2/vacantes-uno.pdf'
PDF_TWO = 'https://ceice.gva.es/documents/1/2/vacantes-dos.pdf'


class DiscoveryPaginationTests(unittest.TestCase):
    def test_query_pages_reach_their_distinct_pdfs(self):
        adapter = RrhhMainDiscoveryAdapter()
        html = {
            ROOT: '<a href="?page=1">Página 1</a><a href="?page=2">Página 2</a>',
            ROOT+'?page=1': f'<h1>Vacantes inicio curso</h1><a href="{PDF_ONE}">Vacantes</a>',
            ROOT+'?page=2': f'<h1>Vacantes inicio curso</h1><a href="{PDF_TWO}">Vacantes</a>',
        }
        def response(url):
            return httpx.Response(200, text=html[url], headers={'content-type':'text/html'}, request=httpx.Request('GET',url))
        with patch.object(adapter, '_fetch_response', side_effect=response) as fetch:
            assets = adapter.discover_assets()
        self.assertEqual({a.canonical_url for a in assets}, {PDF_ONE, PDF_TWO})
        self.assertEqual(fetch.call_count, 3)

    def test_html_query_values_survive_and_fragments_do_not(self):
        adapter = RrhhMainDiscoveryAdapter()
        for query in ('page=2', '_portlet_cur=2&_portlet_delta=20', 'id=42', 'filter=vacantes%20inicio'):
            with self.subTest(query=query):
                self.assertEqual(adapter._canonicalize_url(ROOT+'?'+query+'#content'), ROOT+'?'+query)

    def test_document_cache_keys_and_download_dedup_stay_compatible(self):
        adapter = RrhhMainDiscoveryAdapter()
        for path in (PDF_ONE, 'https://ceice.gva.es/documents/1/2/uuid', 'https://ceice.gva.es/auto/listado'):
            self.assertEqual(adapter._canonicalize_url(path+'?download=true#page=1'), path)

"""Follow both official languages within the same bounded RRHH scope."""
import unittest
from unittest.mock import patch
import httpx
from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter

ROOT = 'https://ceice.gva.es/es/web/rrhh-educacion'
DETAIL = 'https://ceice.gva.es/va/web/rrhh-educacion/-/vacants'
PDF = 'https://ceice.gva.es/documents/1/2/vacants.pdf'


class ValencianDiscoveryTests(unittest.TestCase):
    def test_both_languages_and_root_paths_are_accepted(self):
        p = RrhhMainDiscoveryAdapter()
        for lang in ('es', 'va'):
            for suffix in ('', '/', '/-/vacants', '/adjudicacions?cur=2'):
                with self.subTest(lang=lang, suffix=suffix):
                    self.assertTrue(p._should_follow_html_url(f'https://ceice.gva.es/{lang}/web/rrhh-educacion{suffix}'))

    def test_domain_and_route_boundaries_are_preserved(self):
        p = RrhhMainDiscoveryAdapter()
        for url in ('https://example.invalid/va/web/rrhh-educacion/-/vacants',
                    'https://ceice.gva.es.evil.invalid/va/web/rrhh-educacion',
                    'https://ceice.gva.es/va/web/otra-seccion',
                    'https://ceice.gva.es/es/web/rrhh-educacion-externa',
                    'https://ceice.gva.es/va/web/rrhh-educacion/documento.pdf'):
            with self.subTest(url=url):
                self.assertFalse(p._should_follow_html_url(url))

    def test_valencian_intermediate_reaches_pdf_without_revisiting_spanish_root(self):
        p = RrhhMainDiscoveryAdapter()
        html = {ROOT: f'<a href="{DETAIL}">Vacants inici de curs</a>',
                DETAIL: f'<h1>Vacants inici de curs</h1><a href="{PDF}">Llistat</a><a href="{ROOT}">Castellano</a>'}
        def fetch(url):
            return httpx.Response(200, text=html[url], headers={'content-type':'text/html'}, request=httpx.Request('GET',url))
        with patch.object(p, '_fetch_response', side_effect=fetch) as request:
            assets = p.discover_assets()
        self.assertEqual([a.canonical_url for a in assets], [PDF])
        self.assertEqual(request.call_count, 2)

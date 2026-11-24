"""Bounded ADC detail navigation, offline only."""
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from bs4 import BeautifulSoup
import httpx
from app.services.discovery.non_docent import NonDocentSubstitutionDiscoveryAdapter, SUBSTITUTION_SOURCE_PAGES

DETAIL = 'https://ceice.gva.es/va/web/inclusioeducativa/-/convocatoria-adc'
CALL = 'https://ceice.gva.es/documents/1/2/ADC_EDU_1_26.pdf'
AWARD = 'https://ceice.gva.es/documents/1/2/listadodefinitivo_adc_edu_1_26.pdf'


def response(url, text, status=200):
    return httpx.Response(status, text=text, headers={'content-type':'text/html'}, request=httpx.Request('GET',url))


class NonDocentIntermediateDiscoveryTests(unittest.TestCase):
    def adapter(self):
        return NonDocentSubstitutionDiscoveryAdapter(SUBSTITUTION_SOURCE_PAGES[0])

    def test_detail_with_multiple_documents_and_direct_duplicate(self):
        p = self.adapter()
        root = f'<a href="{DETAIL}">Convocatoria ADC EDU</a><a href="{CALL}">Convocatoria ADC EDU</a>'
        detail = f'<h1>Convocatoria ADC EDU</h1><a href="{CALL}?download=true">Convocatoria</a><a href="{AWARD}">Adjudicación definitiva ADC</a>'
        with patch.object(p, '_get_soup', return_value=BeautifulSoup(root,'html.parser')), patch.object(
                p,'_fetch_response',return_value=response(DETAIL,detail)) as fetch:
            assets=p.discover_assets()
        self.assertEqual({a.canonical_url for a in assets},{CALL,AWARD})
        self.assertEqual({a.asset_role for a in assets},{'non_docent_adc_call_pdf','non_docent_adc_award_pdf'})
        self.assertTrue(all(a.source_key==p.source_key for a in assets))
        fetch.assert_called_once_with(DETAIL)

    def test_external_unrelated_and_cross_collective_navigation_is_ignored(self):
        p=self.adapter()
        urls=['https://example.invalid/es/web/inclusioeducativa/-/adc',
              'https://ceice.gva.es/es/web/otra-seccion/-/adc',
              'https://ceice.gva.es/es/web/inclusioeducativa/personal-no-docent/adjudicacions-eei',
              'https://ceice.gva.es/es/web/inclusioeducativa/-/noticia-general']
        root=''.join(f'<a href="{url}">Enlace</a>' for url in urls)
        with patch.object(p,'_get_soup',return_value=BeautifulSoup(root,'html.parser')),patch.object(p,'_fetch_response') as fetch:
            self.assertEqual(p.discover_assets(),[])
        fetch.assert_not_called()

    def test_second_hop_is_not_followed(self):
        p=self.adapter();root=f'<a href="{DETAIL}">Convocatoria ADC</a>'
        detail='<a href="/es/web/inclusioeducativa/-/otra-adc">Convocatoria ADC</a>'
        with patch.object(p,'_get_soup',return_value=BeautifulSoup(root,'html.parser')),patch.object(
                p,'_fetch_response',return_value=response(DETAIL,detail)) as fetch:
            self.assertEqual(p.discover_assets(),[])
        self.assertEqual(fetch.call_count,1)

    def test_failed_detail_keeps_direct_document_and_error_context(self):
        p=self.adapter();root=f'<a href="{DETAIL}">Convocatoria ADC</a><a href="{CALL}">Convocatoria ADC</a>'
        @contextmanager
        def simulated_stream(*args, **kwargs):
            yield response(DETAIL,'unavailable',500)
        with patch.object(p,'_get_soup',return_value=BeautifulSoup(root,'html.parser')),patch(
                'app.services.discovery.http_policy.PolicyClient.stream', side_effect=simulated_stream):
            assets=p.discover_assets()
        self.assertEqual([a.canonical_url for a in assets],[CALL])
        self.assertEqual(p.discovery_errors[0]['status_code'],500)
        self.assertEqual(p.discovery_errors[0]['url'],DETAIL)

    def test_details_have_a_fixed_request_budget(self):
        p=self.adapter();root=''.join(f'<a href="{DETAIL}-{i}">Convocatoria ADC</a>' for i in range(12))
        with patch.object(p,'_get_soup',return_value=BeautifulSoup(root,'html.parser')),patch.object(
                p,'_fetch_response',side_effect=lambda url:response(url,'<h1>ADC</h1>')) as fetch:
            self.assertEqual(p.discover_assets(),[])
        self.assertEqual(fetch.call_count,10)
        self.assertTrue(any(e['kind']=='page_limit' for e in p.discovery_errors))

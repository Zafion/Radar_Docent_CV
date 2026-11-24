"""Verify a captured HTML-to-document link without claiming source authenticity."""
import hashlib
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def verify_html_link(*, html_bytes, expected_html_sha256, final_page_url, linked_url):
    """Verify a capture and link, not the authenticity of their origin."""
    if not isinstance(html_bytes,bytes) or not 0 < len(html_bytes) <= 5*1024*1024:
        raise ValueError('invalid_html_capture')
    if hashlib.sha256(html_bytes).hexdigest()!=expected_html_sha256:
        raise ValueError('html_sha_mismatch')
    for url in (final_page_url,linked_url):
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('invalid_evidence_url')
    soup=BeautifulSoup(html_bytes,'html.parser')
    bases=soup.find_all('base',href=True)
    if len(bases)>1:
        raise ValueError('ambiguous_html_base')
    base=urljoin(final_page_url,bases[0]['href']) if bases else final_page_url
    base_url=urlsplit(base)
    if base_url.scheme!='https' or not base_url.hostname or base_url.username or base_url.password:
        raise ValueError('invalid_html_base')
    if not any(urljoin(base,a['href'])==linked_url for a in soup.find_all('a',href=True)):
        raise ValueError('document_not_linked_from_capture')


def verify_capture(*, html_bytes, expected_html_sha256, final_page_url,
                   linked_url, pdf_bytes, expected_pdf_sha256):
    """Verify captured bytes and an exact HTML link; no claim about HTTPS origin."""
    verify_html_link(html_bytes=html_bytes,expected_html_sha256=expected_html_sha256,
                     final_page_url=final_page_url,linked_url=linked_url)
    if not isinstance(pdf_bytes,bytes) or not pdf_bytes:
        raise ValueError('invalid_pdf_capture')
    if hashlib.sha256(pdf_bytes).hexdigest()!=expected_pdf_sha256:
        raise ValueError('pdf_sha_mismatch')
    return {'html_sha256':expected_html_sha256,'pdf_sha256':expected_pdf_sha256,
            'link_in_capture':True,'origin_authentication':'not_established_by_capture'}

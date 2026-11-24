import json
from pathlib import Path
import subprocess
import unittest
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from app.services.selective_manifest import validate_technical_manifest
from tests.postgres_isolated_validation import ROOT


class November19EvidenceTests(unittest.TestCase):
    def test_official_html_labels_manifests_and_extracted_text(self):
        base=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/catalogo_historico_documental_20261119/recuperacion_masiva_f3c1dc062dadbcc2'
        overlay={x['url']:x for x in json.loads((base/'catalogo_consolidado_overlay.json').read_text())['items']}
        texts=[]
        for number,label in ((314,'provisional'),(313,'definitivo')):
            folder=base/f'quarantine/document-{number:04}'
            raw=(folder/'manifest_v3.json').read_bytes();entry=json.loads(raw)['entries'][0]
            validate_technical_manifest(folder/'stage',raw,overlay[entry['identity']]['manifest_v3_sha256'])
            ev=entry['evidence'];html=(folder/'stage'/ev['html_file']).read_bytes()
            soup=BeautifulSoup(html,'html.parser')
            labels=[a.get_text(' ',strip=True) for a in soup.find_all('a',href=True)
                    if urljoin(ev['final_page_url'],a['href'])==entry['identity']]
            self.assertEqual(len(labels),1)
            self.assertIn(label,labels[0].casefold())
            self.assertLess(ev['observed_utc'],ev['downloaded_utc'])
            texts.append(subprocess.run(['pdftotext','-layout',str(folder/'stage'/entry['file']),'-'],
                                         capture_output=True,text=True,check=True,timeout=120).stdout)
        self.assertEqual(texts[0].count('18/11/2026'),57)
        self.assertEqual(texts[0].replace('18/11/2026','19/11/2026'),texts[1])


if __name__=='__main__':unittest.main()

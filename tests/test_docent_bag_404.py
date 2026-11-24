"""Local aggregate-only checks for the FC/FP secondary participant format."""
from pathlib import Path
import hashlib
import re
import unittest
from unittest.mock import patch

from pypdf import PdfReader
from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService, FC_FP_MASK_RE

ROOT=Path(__file__).resolve().parents[1]
PDF=ROOT/'auditoria_local/pdfs/ini_2026_par_pro_sec___acf9530d701a.pdf'


class Bag404Tests(unittest.TestCase):
    def setUp(self):
        self.parser=DocentBagParticipantsParserService()
        self.doc={'file_path':str(PDF),'doc_family':'docent_career_practice_participants','list_scope':'secundaria_otros'}

    def test_attached_names_and_both_source_masks(self):
        text="\n".join(["PROFESSORS D'ENSENYAMENT SECUNDARI",'ANGLÉS (411)',
                        '***1234**EXEMPLE, PRIMER 1 PROVISIONALS',
                        '****2345*EXEMPLE, SEGON 2 ESTABILITZATS'])
        with patch.object(self.parser,'_extract_text',return_value=text):
            rows=self.parser._parse_document(self.doc)
        self.assertEqual(len(rows),2)
        self.assertEqual([r.order_number for r in rows],[1,2])
        self.assertEqual([r.masked_dni for r in rows],['***1234**','****2345*'])
        self.assertTrue(all(r.snapshot_key.specialty_code=='411' for r in rows))

    def test_unrecognized_masked_record_is_not_silently_dropped(self):
        text="\n".join(["PROFESSORS D'ENSENYAMENT SECUNDARI",'ANGLÉS (411)',
                        '***1234**EXEMPLE, PRIMER 1 PROVISIONALS',
                        '****2345*UNPARSEABLE'])
        with patch.object(self.parser,'_extract_text',return_value=text):
            with self.assertRaisesRegex(ValueError,'career_participants_extraction_incomplete'):
                self.parser._parse_document(self.doc)

    @unittest.skipUnless(PDF.is_file(),'local PDF required')
    def test_local_pdf_matches_independent_page_markers(self):
        self.assertEqual(hashlib.sha256(PDF.read_bytes()).hexdigest(),'acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0')
        reader=PdfReader(str(PDF)); expected=[]
        for page in reader.pages: expected.extend(FC_FP_MASK_RE.findall(page.extract_text() or ''))
        rows=self.parser._parse_document(self.doc)
        self.assertEqual((len(reader.pages),len(expected),len(rows)),(22,481,481))
        self.assertTrue(expected==[r.masked_dni for r in rows])
        self.assertTrue(all(r.order_number>0 and r.person_display_name and r.collective
                            and r.snapshot_key.body_name and r.snapshot_key.specialty_code
                            and r.service_status is None for r in rows))


if __name__=='__main__':unittest.main()

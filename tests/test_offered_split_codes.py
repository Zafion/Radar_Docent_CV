"""Códigos partidos observados en puestos docentes; muestras sintéticas."""
import unittest
from unittest.mock import patch
from app.services.offered_positions_parser import OfferedPositionsParserService


class OfferedSplitCodeTests(unittest.TestCase):
    def parse_row(self, text, difficult=False):
        parser=OfferedPositionsParserService()
        method=parser._parse_dificil_cobertura_row if difficult else parser._parse_continua_row
        return method(row_text=text,source_type='dificil_cobertura' if difficult else 'continua',
                      body_name=None,specialty_code=None,specialty_name=None,province=None)

    def test_four_plus_two_digits_form_one_position_code(self):
        for difficult in (False,True):
            with self.subTest(difficult=difficult):
                row=self.parse_row('LOCALIDAD - 00000001 - CENTRO SINTÉTICO 7178 46 NO',difficult)
                self.assertIsNotNone(row)
                self.assertEqual(row.position_code,'717846')

    def test_split_code_is_recovered_across_pdf_lines(self):
        parser=OfferedPositionsParserService()
        with patch.object(parser,'_extract_clean_lines',return_value=[
            '1 VACANTE LOCALIDAD - 00000001 - CENTRO SINTÉTICO 7178', '46 NO']):
            rows=parser._parse_document({'file_path':'synthetic.pdf','list_scope':'continua'})
        self.assertEqual(rows[0].position_code,'717846')

    def test_contiguous_six_digits_still_work(self):
        self.assertEqual(self.parse_row('LOCALIDAD - 00000001 - CENTRO SINTÉTICO 717846 NO').position_code,'717846')

    def test_long_number_inside_center_name_is_not_a_position(self):
        row=self.parse_row('LOCALIDAD - 00000001 - CENTRO NÚMERO 12345678 717846 NO')
        self.assertEqual(row.position_code,'717846')

    def test_seven_digit_number_is_not_truncated(self):
        self.assertIsNone(self.parse_row('LOCALIDAD - 00000001 - CENTRO SINTÉTICO 1234567 NO'))

    def test_other_observed_splits_are_recovered(self):
        for split,expected in [('84 2021','842021'),('5 13362','513362'),('786 586','786586')]:
            with self.subTest(split=split):
                row=self.parse_row(f'LOCALIDAD - 00000001 - CENTRO SINTÉTICO {split} NO')
                self.assertEqual(row.position_code,expected)

    def test_numbered_center_glued_to_position_keeps_both(self):
        row=self.parse_row('LOCALIDAD - 00000001 - CENTRO SINTÉTICO NÚMERO 2899658 NO')
        self.assertEqual(row.position_code,'899658')
        self.assertEqual(row.center_name,'CENTRO SINTÉTICO NÚMERO 2')

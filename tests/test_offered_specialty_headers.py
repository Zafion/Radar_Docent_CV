"""Especialidad antes/después de etiqueta y nombres sin código."""
import unittest
from app.services.offered_positions_parser import OfferedPositionsParserService


class OfferedSpecialtyHeaderTests(unittest.TestCase):
    def test_code_before_label_with_or_without_spaces(self):
        parser=OfferedPositionsParserService()
        for code in ('2A2','206','3A5'):
            for gap in ('',' '):
                with self.subTest(code=code,gap=gap):
                    self.assertEqual(parser._parse_specialty(f'{code}{gap}ESPECIALIDAD/ESPECIALITAT: INSTALACIONES ELECTROTÉCNICAS'),(code,'INSTALACIONES ELECTROTÉCNICAS'))

    def test_legacy_code_after_label_remains_supported(self):
        parser=OfferedPositionsParserService()
        for separator in (' - ',' '):
            self.assertEqual(parser._parse_specialty(f'ESPECIALIDAD/ESPECIALITAT: 120{separator}EDUCACIÓN INFANTIL'),('120','EDUCACIÓN INFANTIL'))

    def test_specialty_name_is_not_invented_as_code(self):
        self.assertEqual(OfferedPositionsParserService()._parse_specialty(
            'ESPECIALIDAD/ESPECIALITAT: INSTALACIONES ELECTROTÉCNICAS'),(None,'INSTALACIONES ELECTROTÉCNICAS'))

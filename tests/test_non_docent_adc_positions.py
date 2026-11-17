"""ADC: códigos de puesto, sin datos personales ni acceso a bases."""
import unittest
from app.services.non_docent_parser import NonDocentParserService


class NonDocentAdcPositionTests(unittest.TestCase):
    def rows(self, code):
        return NonDocentParserService()._extract_adc_award_rows(
            f'4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No {code} - CENTRO SINTETICO')

    def test_accepts_three_through_six_digits(self):
        for code in ('123', '1234', '12345', '123456'):
            with self.subTest(code=code):
                rows=self.rows(code)
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['position_code'],code)

    def test_rejects_other_lengths_and_non_numeric_codes(self):
        for code in ('12','1234567','ABC','123A','12 345'):
            with self.subTest(code=code):
                self.assertEqual(self.rows(code),[])

    def test_two_awards_are_not_combined(self):
        text=('4-E. TERAPIA OCUPACIONAL 51.95 AE APELLIDO SINTETICO NOMBRE No 123 - CENTRO UNO '
              '5-E. OTRA BOLSA 49.50 AE OTRA PERSONA SINTETICA No 123456 - CENTRO DOS')
        rows=NonDocentParserService()._extract_adc_award_rows(text)
        self.assertEqual([r['position_code'] for r in rows],['123','123456'])

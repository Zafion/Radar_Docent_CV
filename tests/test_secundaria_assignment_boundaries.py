"""Alphanumeric specialty labels must not split teacher assignment blocks."""
import unittest
from unittest.mock import patch
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService


class SecundariaAssignmentBoundaryTests(unittest.TestCase):
    def test_specialty_with_comma_is_not_a_person_start(self):
        p = FinalAwardListingSecundariaParserService()
        self.assertFalse(p._is_entry_start(['2A5 / MAQUINES, SERVEIS I PRODUCCIO'], 0))

    def test_specialty_keeps_its_own_center_and_person(self):
        p = FinalAwardListingSecundariaParserService()
        lines = ['1', 'VACANT', 'LOCALIDAD(00000001)CENTRO UNO', '240 / ESPECIALIDAD UNO',
                 '123456', 'Jornada completa', 'APELLIDO UNO, NOMBRE Petición: Voluntaria1', 'Adjudicat',
                 '2', 'VACANT', 'OTRA LOCALIDAD(00000002)CENTRO DOS',
                 '2A5 / MAQUINES, SERVEIS I PRODUCCIO', '234567', 'Jornada completa',
                 'APELLIDO DOS, NOMBRE Petición: Voluntaria1', 'Adjudicat']
        with patch.object(p, '_extract_clean_lines', return_value=lines):
            rows = p._parse_document({'file_path': 'synthetic.pdf'})
        self.assertEqual(len(rows), 2)
        self.assertEqual([r.person_display_name for r in rows], ['APELLIDO UNO, NOMBRE', 'APELLIDO DOS, NOMBRE'])
        self.assertEqual([r.assignment.center_code for r in rows], ['00000001', '00000002'])
        self.assertEqual([r.assignment.position_code for r in rows], ['123456', '234567'])
        self.assertEqual(rows[1].assignment.position_specialty_code, '2A5')

    def test_fractional_order_and_glued_name_remain_supported(self):
        p = FinalAwardListingSecundariaParserService()
        for label in ('12 / 34 APELLIDO SINTETICO, NOMBRE', '12APELLIDO SINTETICO, NOMBRE'):
            with self.subTest(label=label):
                self.assertTrue(p._is_entry_start([label, 'No adjudicat'], 0))
                with patch.object(p, '_extract_clean_lines', return_value=[label, 'No adjudicat']):
                    rows = p._parse_document({'file_path': 'synthetic.pdf'})
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0].order_number, 12)

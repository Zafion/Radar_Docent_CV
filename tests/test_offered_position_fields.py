"""Columnas de plazas: no convertir itinerancia u observaciones en otros datos."""
import unittest
from unittest.mock import patch
from app.services.offered_positions_parser import OfferedPositionsParserService


class OfferedPositionFieldTests(unittest.TestCase):
    def test_alphanumeric_center_is_a_separate_source_row(self):
        parser=OfferedPositionsParserService()
        lines=['2696 VACANTEALACANT - 03001477 - CEP CENTRO DE RECEPCIÓN 920357 SI',
               '2697 VACANTEALACANT - 03B39521 - CIDA-CENTRO DE INNOVACIÓN DIGITAL APLICADA865008 NO',
               '2698 VACANTEALACANT - 03009968 - EI EL TOSSALET 919934 NO']
        with patch.object(parser,'_extract_clean_lines',return_value=lines):
            rows=parser._parse_document({'file_path':'fixture.pdf','list_scope':'inicio_curso'})
        self.assertEqual([(r.center_code,r.position_code) for r in rows],
                         [('03001477','920357'),('03B39521','865008'),('03009968','919934')])
        self.assertNotIn('03B39521',rows[0].raw_row_text)

    def row(self, text, difficult=False):
        p=OfferedPositionsParserService()
        method=p._parse_dificil_cobertura_row if difficult else p._parse_continua_row
        return method(row_text=text,source_type='dificil_cobertura' if difficult else 'continua',
                      body_name=None,specialty_code=None,specialty_name=None,province=None)

    def test_yes_no_is_itinerancy_not_a_language_requirement(self):
        for flag,expected in [('SI',True),('NO',False)]:
            with self.subTest(flag=flag):
                row=self.row(f'1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 {flag}')
                self.assertIs(row.is_itinerant,expected)
                self.assertIsNone(row.valenciano_required_text)
                self.assertIsNone(row.hours_value)

    def test_language_and_decimal_hours_after_itinerancy(self):
        row=self.row('1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 NO ING-B2. 11,5')
        self.assertFalse(row.is_itinerant)
        self.assertEqual(row.valenciano_required_text,'ING-B2.')
        self.assertEqual(row.hours_value,11.5)

    def test_hours_before_itinerancy_are_explicit_fields(self):
        row=self.row('1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 18 SI')
        self.assertEqual(row.hours_value,18)
        self.assertTrue(row.is_itinerant)

    def test_difficult_hours_before_locality_with_or_without_gap(self):
        for gap in ('',' '):
            with self.subTest(gap=gap):
                row=self.row(f'18{gap}LOCALIDAD - 00000001 - CENTRO NO ING-B2. 123456 Sust. Ind.',True)
                self.assertEqual(row.hours_value,18)
                self.assertEqual(row.locality,'LOCALIDAD')
                self.assertEqual(row.center_name,'CENTRO')
                self.assertFalse(row.is_itinerant)
                self.assertEqual(row.valenciano_required_text,'ING-B2.')

    def test_center_number_is_not_working_hours(self):
        row=self.row('LOCALIDAD - 00000001 - CENTRO NÚMERO 16 NO 123456 Vacante',True)
        self.assertIsNone(row.hours_value)
        self.assertEqual(row.center_name,'CENTRO NÚMERO 16')

    def test_ages_in_observations_are_not_hours(self):
        row=self.row('1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 NO INFANTIL 0 A 3 AÑOS')
        self.assertIsNone(row.hours_value)
        self.assertIn('0 A 3 AÑOS',row.observations)

    def test_multicenter_composition_is_kept_on_one_position_only(self):
        p=OfferedPositionsParserService()
        lines=['1 SUSTITUCIÓN DETERMINADA',
            '00000001: CENTRO UNO (LOCALIDAD) 9,00 hores ESPECIALIDAD',
            '00000002: CENTRO DOS (OTRA LOCALIDAD) 9,00 hores ESPECIALIDAD',
            'LOCALIDAD - 00000001 - CENTRO UNO 123456 SI',
            '2 VACANTE OTRA LOCALIDAD - 00000003 - CENTRO TRES 234567 NO']
        with patch.object(p,'_extract_clean_lines',return_value=lines):
            rows=p._parse_document({'file_path':'synthetic.pdf','list_scope':'continua'})
        self.assertEqual(len(rows),2)
        self.assertIn('00000001',rows[0].composition)
        self.assertIn('00000002',rows[0].composition)
        self.assertEqual(rows[0].composition.count('9,00 hores'),2)
        self.assertEqual(rows[0].position_code,'123456')
        self.assertIsNone(rows[0].hours_value)  # no inventar una jornada total
        self.assertIsNone(rows[1].composition)

    def test_inline_composition_is_not_dropped(self):
        row=self.row('1 VACANTE LOCALIDAD - 00000001 - CENTRO UNO 123456 SI 00000002: CENTRO DOS 9,00 hores')
        self.assertIn('00000002',row.composition)
        self.assertIsNone(row.hours_value)

    def test_explicit_valencian_level_is_kept(self):
        for value in ('C2 Val.','C2Val.'):
            with self.subTest(value=value):
                row=self.row(f'1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 NO {value}')
                self.assertEqual(row.valenciano_required_text,value)
                self.assertFalse(row.is_itinerant)
                self.assertIsNone(row.hours_value)

    def test_itinerancy_glued_to_valencian_requirement(self):
        for flag, expected in [('SI', True), ('NO', False)]:
            with self.subTest(flag=flag):
                row=self.row(f'1 VACANTE LOCALIDAD - 00000001 - CENTRO 123456 {flag}C2 Val.')
                self.assertIs(row.is_itinerant, expected)
                self.assertEqual(row.valenciano_required_text, 'C2 Val.')

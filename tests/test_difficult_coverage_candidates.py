"""Difícil cobertura: candidatos sintéticos y separación por puesto."""
import unittest
from unittest.mock import patch
from app.services import difficult_coverage_provisional_parser as module


class DifficultCoverageCandidateTests(unittest.TestCase):
    def test_glued_row_number_is_not_part_of_surname(self):
        result = module.DifficultCoverageProvisionalParserService()._parse_candidate_row(
            '--> APELLIDO SINTETICO1 NOMBRE 29/10/2026 17:09:01REG/2026/1234567 X 1 S 2 123456')
        self.assertIsNotNone(result)
        self.assertEqual(result.row_number, 1)
        self.assertEqual(result.full_name, 'APELLIDO SINTETICO NOMBRE')
        self.assertEqual(result.first_name, 'NOMBRE')
        self.assertEqual(result.petition_number, 1)
        self.assertEqual(result.assigned_position_code, '123456')
        self.assertTrue(result.is_selected)

    def test_compound_surname_does_not_shift_first_name(self):
        result = module.DifficultCoverageProvisionalParserService()._parse_candidate_row(
            'DE LA PRUEBA SINTETICA12 NOMBRE COMPUESTO 29/10/2026 17:09:01REG/2026/1234567 3 S 3')
        self.assertIsNotNone(result)
        self.assertEqual(result.first_name, 'NOMBRE COMPUESTO')
        self.assertEqual(result.full_name, 'DE LA PRUEBA SINTETICA NOMBRE COMPUESTO')
        self.assertIsNone(result.last_name_1)
        self.assertIsNone(result.last_name_2)

    def test_legacy_row_with_terminal_order_remains_supported(self):
        result = module.DifficultCoverageProvisionalParserService()._parse_candidate_row(
            'APELLIDO SINTETICO NOMBRE 1 29/10/2026 17:09:01 REG/2026/1234567 1 S 3')
        self.assertEqual(result.row_number, 1)
        self.assertEqual(result.full_name, 'APELLIDO SINTETICO NOMBRE')

    def test_candidate_stays_with_its_position_across_repeated_headers(self):
        parser = module.DifficultCoverageProvisionalParserService()
        header1='LOCALIDAD CEIP CENTRO123456 00000001PUESTO :'
        header2='OTRA LOCALIDAD IES CENTRO234567 00000002PUESTO :'
        lines=['1', header1,
            'APELLIDO SINTETICO1 NOMBRE 29/10/2026 17:09:01REG/2026/1234567 1 S 3',
            '4394 30/10/2026 07:35:23', '1', header1,
            '1', header2,
            'OTRO APELLIDO1 OTRA 29/10/2026 17:09:01REG/2026/1234568 2 S 3']
        with patch.object(parser, '_extract_clean_lines', return_value=lines):
            result=parser._parse_document({'file_path':'synthetic.pdf'})
        self.assertEqual([p.position_code for p,c in result], ['123456','234567'])
        self.assertEqual([c[0].full_name for p,c in result], ['APELLIDO SINTETICO NOMBRE','OTRO APELLIDO OTRA'])
        self.assertEqual([len(c) for p,c in result], [1,1])

    def test_missing_candidate_order_detects_partial_parse(self):
        parser=module.DifficultCoverageProvisionalParserService()
        with patch.object(parser,'_extract_clean_lines',return_value=[
            '2','LOCALIDAD CEIP CENTRO123456 00000001PUESTO :',
            'APELLIDO SINTETICO2 NOMBRE 29/10/2026 17:09:01REG/2026/1234567 1 S 3']):
            with self.assertRaisesRegex(ValueError,'candidate_order_gaps'):
                parser._parse_document({'file_path':'synthetic.pdf'})

    def test_header_must_not_accept_seven_digit_position(self):
        self.assertIsNone(module.POSITION_LINE_RE.match('LOCALIDAD CEIP CENTRO1234567 00000001PUESTO :'))

    def test_numbered_center_name_is_not_part_of_position(self):
        match=module.POSITION_LINE_RE.match('LOCALIDADCEIP NÚMERO 16123456 00000001PUESTO :')
        self.assertIsNotNone(match)
        self.assertEqual(match['position_code'], '123456')
        self.assertTrue(match['prefix'].endswith('16'))

    def test_header_counter_is_not_assumed_to_be_position_row_count(self):
        parser=module.DifficultCoverageProvisionalParserService()
        with patch.object(parser,'_extract_clean_lines',return_value=[
            '2013','LOCALIDAD CEIP CENTRO123456 00000001PUESTO :',
            'APELLIDO SINTETICO1 NOMBRE 29/10/2026 17:09:01REG/2026/1234567 1 S 3']):
            result=parser._parse_document({'file_path':'synthetic.pdf'})
        self.assertEqual(len(result[0][1]),1)

    def test_position_headers_with_no_candidates_are_not_success(self):
        parser=module.DifficultCoverageProvisionalParserService()
        with patch.object(parser,'_extract_clean_lines',return_value=[
            'LOCALIDAD CEIP CENTRO123456 00000001PUESTO :']):
            with self.assertRaisesRegex(ValueError,'no_candidates_parsed'):
                parser._parse_document({'file_path':'synthetic.pdf'})

"""Validación previa a reemplazar resultados: ejemplos mínimos sin datos reales."""
import unittest
from unittest.mock import Mock, patch

from app.services import final_award_listing_maestros_parser as module


PERSON = 'PERSONA SINTÉTICA, NOMBRE'
OTHER = 'OTRA PERSONA SINTÉTICA, NOMBRE'
VALID_AWARD = [
    '1', 'VACANT', 'LOCALIDAD(00000001)CENTRO SINTÉTICO',
    '128 / EDUCACIÓN PRIMARIA', '123456',
    f'{PERSON} Petición: Voluntaria 2', 'Adjudicat',
]


class MaestrosCompletenessTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.final_award_listing_maestros_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def parse(self, lines):
        parser = module.FinalAwardListingMaestrosParserService()
        with patch.object(parser, '_extract_clean_lines', return_value=lines):
            return parser._parse_document({'file_path': 'synthetic.pdf'})

    def invalid(self, lines):
        with self.assertRaisesRegex(ValueError, 'maestros_extraction_validation_failed') as error:
            self.parse(lines)
        # Failure diagnostics must never include people or raw PDF blocks.
        self.assertNotIn(PERSON, str(error.exception))
        self.assertNotIn(OTHER, str(error.exception))
        return error.exception

    def test_nonempty_extraction_cannot_ignore_a_rejected_block(self):
        error = self.invalid([
            f'1 {PERSON}', 'No adjudicat', f'2 {OTHER}', 'Resultado no reconocido',
        ])
        self.assertEqual(error.metrics['parsed_rows'], 1)
        self.assertEqual(error.metrics['rejected_blocks'], 1)
        self.assertEqual(error.metrics['rejected_block_numbers'], [2])

    def test_status_before_first_block_must_not_be_lost(self):
        error = self.invalid(['Desactivat', f'1 {PERSON}', 'No adjudicat'])
        self.assertIn('status_counts_mismatch', error.metrics['issues'])
        self.assertEqual(error.metrics['source_status_counts']['Desactivat'], 1)

    def test_multiple_statuses_in_same_block_are_not_one_person(self):
        error = self.invalid([f'1 {PERSON}', 'No adjudicat', 'Desactivat'])
        self.assertEqual(error.metrics['ambiguous_blocks'], 1)

    def test_award_without_center_is_rejected(self):
        error = self.invalid([line for line in VALID_AWARD if '(00000001)' not in line])
        self.assertEqual(error.metrics['missing_fields']['center_code'], 1)

    def test_award_without_position_is_rejected(self):
        error = self.invalid([line for line in VALID_AWARD if line != '123456'])
        self.assertEqual(error.metrics['missing_fields']['position_code'], 1)

    def test_missing_order_or_name_is_rejected(self):
        row = module.ParsedAwardResult(None, '', '', 'No adjudicat', 'synthetic', None)
        parser = module.FinalAwardListingMaestrosParserService()
        with patch.object(parser, '_extract_clean_lines', return_value=[f'1 {PERSON}', 'No adjudicat']), patch.object(parser, '_parse_block', return_value=row):
            with self.assertRaisesRegex(ValueError, 'maestros_extraction_validation_failed') as error:
                parser._parse_document({'file_path': 'synthetic.pdf'})
        self.assertEqual(error.exception.metrics['missing_fields']['order_number'], 1)
        self.assertEqual(error.exception.metrics['missing_fields']['person_name'], 1)

    def test_matching_total_with_wrong_status_distribution_is_rejected(self):
        parser = module.FinalAwardListingMaestrosParserService()
        rows = [module.ParsedAwardResult(i, PERSON, 'synthetic', 'No adjudicat', 'synthetic', None) for i in (1, 2)]
        lines = [f'1 {PERSON}', 'No adjudicat', f'2 {OTHER}', 'Desactivat']
        with patch.object(parser, '_extract_clean_lines', return_value=lines), patch.object(parser, '_parse_block', side_effect=rows):
            with self.assertRaisesRegex(ValueError, 'maestros_extraction_validation_failed') as error:
                parser._parse_document({'file_path': 'synthetic.pdf'})
        self.assertIn('status_counts_mismatch', error.exception.metrics['issues'])
        self.assertEqual(error.exception.metrics['parsed_rows'], 2)

    def test_awarded_row_requires_an_assignment(self):
        parser = module.FinalAwardListingMaestrosParserService()
        row = module.ParsedAwardResult(1, PERSON, 'synthetic', 'Adjudicat', 'synthetic', None)
        with patch.object(parser, '_extract_clean_lines', return_value=[f'1 {PERSON}', 'Adjudicat']), patch.object(parser, '_parse_block', return_value=row):
            with self.assertRaisesRegex(ValueError, 'maestros_extraction_validation_failed') as error:
                parser._parse_document({'file_path': 'synthetic.pdf'})
        self.assertEqual(error.exception.metrics['missing_fields']['assignment'], 1)

    def test_valid_awarded_and_unawarded_rows_do_not_require_unknown_hours(self):
        rows = self.parse(VALID_AWARD + [f'3 {OTHER}', 'No adjudicat'])
        self.assertEqual(len(rows), 2)
        self.assertIsNone(rows[0].assignment.hours_value)
        self.assertIsNone(rows[1].assignment)

    def test_failed_validation_preserves_rows_and_next_document_can_succeed(self):
        store = Mock()
        store.list_final_listing_documents.return_value = [
            {'document_id': i, 'document_version_id': i,
             'file_path': f'{i}.pdf', 'original_filename': f'synthetic-{i}.pdf'}
            for i in (1, 2)
        ]
        parser = module.FinalAwardListingMaestrosParserService()
        contents = [
            [f'1 {PERSON}', 'No adjudicat', f'2 {OTHER}', 'Resultado no reconocido'],
            VALID_AWARD,
        ]
        with patch.object(module, 'AwardResultsStore', return_value=store), patch.object(parser, '_extract_clean_lines', side_effect=contents):
            result = parser.parse_all_documents()
        self.assertEqual([row['status'] for row in result], ['failed', 'success'])
        self.assertEqual(result[0]['validation']['rejected_blocks'], 1)
        store.clear_award_results_for_document.assert_called_once_with(2)
        self.assertEqual(store.mark_document_parsed.call_args.kwargs['document_id'], 2)
        failed_run = store.finish_parse_run.call_args_list[0].kwargs
        self.assertEqual(failed_run['status'], 'failed')
        self.assertNotIn(PERSON, failed_run['error_message'])

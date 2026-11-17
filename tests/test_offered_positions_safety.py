"""Protección de plazas ofertadas ante ceros; sin red ni bases persistentes."""
import unittest
from unittest.mock import Mock, patch

from app.services import offered_positions_parser as module
from app.storage.offered_positions_store import OfferedPositionsStore


VALID_ROW = '1 VACANTE LOCALIDAD - 00000001 - CENTRO SINTÉTICO 123456'


class OfferedPositionsSafetyTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.offered_positions_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def run_documents(self, contents):
        store = Mock()
        store.list_offered_position_documents.return_value = [
            {'document_id': i, 'document_version_id': i, 'original_filename': f'synthetic-{i}.pdf',
             'file_path': f'{i}.pdf', 'list_scope': 'continua'}
            for i in range(1, len(contents) + 1)
        ]
        parser = module.OfferedPositionsParserService()
        with patch.object(module, 'OfferedPositionsStore', return_value=store), patch.object(parser, '_extract_clean_lines', side_effect=contents):
            result = parser.parse_all_documents()
        return store, result

    def test_unexpected_empty_return_never_replaces_existing_positions(self):
        store = Mock()
        store.list_offered_position_documents.return_value = [
            {'document_id': 1, 'document_version_id': 1, 'original_filename': 'synthetic.pdf'}]
        with patch.object(module, 'OfferedPositionsStore', return_value=store), patch.object(module.OfferedPositionsParserService, '_parse_document', return_value=[]):
            result = module.OfferedPositionsParserService().parse_all_documents()
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_offered_positions_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_unsupported_document_does_not_stop_next_document(self):
        store, result = self.run_documents([
            ['NORMATIVA DEL CURSO', 'Artículo primero. Objeto.'], [VALID_ROW]])
        self.assertEqual([row['status'] for row in result], ['skipped', 'success'])
        self.assertEqual(result[0]['reason'], 'unsupported_format')
        self.assertNotIn('error', result[0])
        store.clear_offered_positions_for_document.assert_called_once_with(2)
        self.assertEqual(store.mark_document_parsed.call_args.kwargs['document_id'], 2)

    def test_explicit_empty_notice_preserves_previous_positions(self):
        store, result = self.run_documents([['PLAZAS OFERTADAS', 'No hay plazas ofertadas.']])
        self.assertEqual(result[0]['status'], 'skipped')
        self.assertEqual(result[0]['reason'], 'explicitly_empty')
        store.clear_offered_positions_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_unreadable_document_fails(self):
        store, result = self.run_documents([[]])
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_offered_positions_for_document.assert_not_called()

    def test_unparseable_candidate_row_is_not_a_legitimate_zero(self):
        store, result = self.run_documents([['PLAZAS OFERTADAS', '1 VACANTE LOCALIDAD - 00000001 - CENTRO SINTÉTICO SIN CÓDIGO']])
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_offered_positions_for_document.assert_not_called()

    def test_offer_heading_without_rows_or_empty_notice_fails(self):
        store, result = self.run_documents([['PLAZAS OFERTADAS', 'Formato cambiado']])
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_offered_positions_for_document.assert_not_called()

    def test_empty_claim_alone_does_not_establish_document_type(self):
        _, result = self.run_documents([['No hay plazas ofertadas.']])
        self.assertEqual(result[0].get('reason'), 'unsupported_format')

    def test_actual_rows_take_precedence_over_empty_notice(self):
        store, result = self.run_documents([['PLAZAS OFERTADAS', 'No hay plazas ofertadas.', VALID_ROW]])
        self.assertEqual(result[0]['status'], 'success')
        self.assertEqual(result[0]['rows_extracted'], 1)
        store.clear_offered_positions_for_document.assert_called_once_with(1)

    def test_failure_metadata_survives_rollback(self):
        store, result = self.run_documents([[]])
        self.assertEqual(result[0]['status'], 'failed')
        names = [call[0] for call in store.mock_calls]
        self.assertLess(names.index('connection.commit'), names.index('connection.rollback'))
        self.assertLess(names.index('connection.rollback'), names.index('finish_parse_run'))

    def test_prior_attempt_requires_plan_across_versions(self):
        from app.services.parse_selection import reason, Selection
        for status in ('success', 'skipped', 'failed', 'running'):
            item = dict(doc_family='offered_positions', latest_run_id=1, parsed_at=None)
            for version in (module.PARSER_VERSION, 'future-version'):
                self.assertEqual(reason(item, module.PARSER_KEY, version, Selection()),
                                 'previous_attempt_requires_plan')

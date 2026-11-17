"""Protecciones transaccionales de maestros, sin PDFs ni bases reales."""
import unittest
import io
import runpy
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services import final_award_listing_maestros_parser as module
from app.storage.award_results_store import AwardResultsStore


class MaestrosParseSafetyTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.final_award_listing_maestros_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def run_documents(self, contents):
        store = Mock()
        store.list_final_listing_documents.return_value = [
            {'document_id': i, 'document_version_id': i,
             'original_filename': f'synthetic-{i}.pdf', 'file_path': f'{i}.pdf'}
            for i in range(1, len(contents) + 1)
        ]
        store.create_parse_run.side_effect = range(1, len(contents) + 1)
        parser = module.FinalAwardListingMaestrosParserService()
        with patch.object(module, 'AwardResultsStore', return_value=store), patch.object(
            parser, '_extract_clean_lines', side_effect=contents
        ):
            result = parser.parse_all_documents()
        return store, result

    def test_misclassified_document_does_not_fail_following_document(self):
        store, result = self.run_documents([
            ['NORMATIVA DE INICIO DE CURSO', 'Artículo primero. Objeto.'],
            ['1 PERSONA SINTÉTICA, NOMBRE', 'No adjudicat'],
        ])
        self.assertEqual([r['status'] for r in result], ['skipped', 'success'])
        self.assertEqual(result[0]['reason'], 'unsupported_format')
        self.assertNotIn('error', result[0])
        store.clear_award_results_for_document.assert_called_once_with(2)
        store.mark_document_parsed.assert_called_once()
        self.assertEqual(store.mark_document_parsed.call_args.kwargs['document_id'], 2)

    def test_explicit_empty_is_not_a_parser_failure_or_a_replacement(self):
        store, result = self.run_documents([
            ['ADJUDICACIONES DE MAESTROS', 'No hay adjudicaciones.'],
        ])
        self.assertEqual(result[0]['status'], 'skipped')
        self.assertEqual(result[0]['reason'], 'explicitly_empty')
        store.clear_award_results_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_career_layout_with_name_blocks_is_skipped_until_supported(self):
        store, result = self.run_documents([[
            'ADJUDICACIÓ COS DE MESTRES 2026',
            'Adjudicat per la petició número 2. Lloc: 123456',
            '1 PERSONA SINTÉTICA, NOMBRE',
        ]])
        self.assertEqual(result[0]['status'], 'skipped')
        self.assertEqual(result[0]['reason'], 'unsupported_format')
        store.clear_award_results_for_document.assert_not_called()

    def test_empty_notice_does_not_override_actual_results(self):
        store, result = self.run_documents([[
            'ADJUDICACIONES DE MAESTROS', 'No hay adjudicaciones.',
            '1 PERSONA SINTÉTICA, NOMBRE', 'No adjudicat',
        ]])
        self.assertEqual(result[0]['status'], 'success')
        store.clear_award_results_for_document.assert_called_once_with(1)

    def test_empty_claim_without_document_context_is_unsupported(self):
        _, result = self.run_documents([['No hay adjudicaciones.']])
        self.assertEqual(result[0].get('reason'), 'unsupported_format')

    def test_unreadable_text_is_failed_and_preserves_existing_rows(self):
        store, result = self.run_documents([[]])
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_award_results_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_recognized_but_unparseable_document_is_failed(self):
        store, result = self.run_documents([['Adjudicat']])
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_award_results_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_failure_run_survives_rollback_of_replacement(self):
        # The run must be committed before parsing: otherwise rollback removes
        # its INSERT and the subsequent finish UPDATE silently updates no row.
        store, _ = self.run_documents([[]])
        names = [call[0] for call in store.mock_calls]
        self.assertLess(names.index('connection.commit'), names.index('connection.rollback'))
        self.assertLess(names.index('connection.rollback'), names.index('finish_parse_run'))
        self.assertEqual(store.finish_parse_run.call_args.kwargs['status'], 'failed')

    def test_prior_attempt_requires_plan_across_parser_keys_and_versions(self):
        from app.services.parse_selection import reason, Selection
        for status in ('success', 'skipped', 'failed', 'running'):
            item = dict(doc_family='final_award_listing', latest_run_id=1, parsed_at=None)
            for key, version in ((module.PARSER_KEY, module.PARSER_VERSION),
                                 ('another-parser', 'future-version')):
                self.assertEqual(reason(item, key, version, Selection()),
                                 'previous_attempt_requires_plan')

    def test_unexpected_empty_result_never_clears_existing_rows(self):
        store = Mock()
        store.list_final_listing_documents.return_value = [{
            'document_id': 1, 'document_version_id': 1, 'original_filename': 'synthetic.pdf',
        }]
        with patch.object(module, 'AwardResultsStore', return_value=store), patch.object(
            module.FinalAwardListingMaestrosParserService, '_parse_document', return_value=[]
        ):
            result = module.FinalAwardListingMaestrosParserService().parse_all_documents()
        self.assertEqual(result[0]['status'], 'failed')
        store.clear_award_results_for_document.assert_not_called()
        store.mark_document_parsed.assert_not_called()

    def test_parser_entrypoint_does_not_exit_for_skipped_document(self):
        summary = SimpleNamespace(
            parser_name='final_award_listing_maestros', documents_processed=1,
            success_count=0, failed_count=0,
            raw_results=[{'document_id': 1, 'original_filename': 'synthetic.pdf',
                          'rows_extracted': 0, 'status': 'skipped', 'reason': 'unsupported_format'}],
        )
        dispatcher = Mock()
        dispatcher.run.return_value = SimpleNamespace(parser_summaries=[summary], matcher_executed=False)
        with patch('app.services.document_parse_dispatcher.DocumentParseDispatcherService', return_value=dispatcher), redirect_stdout(io.StringIO()):
            with patch('sys.argv',['run_parse_documents.py']):
                runpy.run_path('run_parse_documents.py', run_name='__main__')

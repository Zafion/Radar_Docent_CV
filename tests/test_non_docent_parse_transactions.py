"""Non-docent parse attempts survive rollback without committing business writes."""
import unittest
from unittest.mock import Mock, patch

from app.services import non_docent_parser as module


class NonDocentParseTransactionTests(unittest.TestCase):
    def setUp(self):
        # Parser transactions are isolated; claims have real PostgreSQL coverage.
        claim = patch("app.services.non_docent_parser.locked_documents",
                      side_effect=lambda store, rows, key, version: rows)
        claim.start()
        self.addCleanup(claim.stop)

    def run_parser(self, outcomes):
        store = Mock()
        store.list_documents_for_parser.return_value = [
            dict(document_id=i, document_version_id=i, doc_family='non_docent_adc_award',
                 original_filename=f'synthetic-{i}.pdf') for i in range(1, len(outcomes)+1)]
        store.create_parse_run.side_effect = range(1, len(outcomes)+1)
        service = module.NonDocentParserService()
        with patch.object(module, 'NonDocentStore', return_value=store), patch.object(
                service, '_parse_document', side_effect=outcomes) as parse:
            result = service.parse_all_documents()
        return store, parse, result

    def test_attempt_committed_before_parsing_and_failure_rollback(self):
        store, _, result = self.run_parser([RuntimeError('synthetic_failure')])
        calls = [c[0] for c in store.mock_calls]
        self.assertLess(calls.index('create_parse_run'), calls.index('connection.commit'))
        self.assertLess(calls.index('connection.commit'), calls.index('connection.rollback'))
        self.assertLess(calls.index('connection.rollback'), calls.index('finish_parse_run'))
        self.assertEqual(result[0]['status'], 'failed')
        self.assertEqual(store.finish_parse_run.call_args.kwargs['parse_run_id'], 1)
        self.assertEqual(store.finish_parse_run.call_args.kwargs['status'], 'failed')
        store.mark_document_parsed.assert_not_called()
        self.assertEqual(store.connection.commit.call_count, 2)

    def test_next_document_is_processed_after_failure(self):
        store, parse, result = self.run_parser([RuntimeError('synthetic_failure'), module.ParseResult(1, 20)])
        self.assertEqual([r['status'] for r in result], ['failed', 'success'])
        self.assertEqual(parse.call_count, 2)
        store.mark_document_parsed.assert_called_once()
        self.assertEqual(store.mark_document_parsed.call_args.kwargs['document_id'], 2)
        self.assertEqual(store.connection.rollback.call_count, 1)

    def test_success_commits_attempt_then_business_data(self):
        store, _, result = self.run_parser([module.ParseResult(1, 10)])
        calls = [c[0] for c in store.mock_calls]
        self.assertLess(calls.index('connection.commit'), calls.index('finish_parse_run'))
        self.assertLess(calls.index('finish_parse_run'), calls.index('mark_document_parsed'))
        self.assertEqual(store.connection.commit.call_count, 2)
        store.connection.rollback.assert_not_called()
        self.assertEqual(result[0]['status'], 'success')

    def test_failed_audit_update_does_not_mark_document_parsed(self):
        store = Mock()
        store.list_documents_for_parser.return_value = [dict(document_id=1, document_version_id=1,
            doc_family='non_docent_adc_award', original_filename='synthetic.pdf')]
        store.create_parse_run.return_value = 1
        store.finish_parse_run.side_effect = RuntimeError('synthetic_audit_storage_failure')
        service = module.NonDocentParserService()
        with patch.object(module, 'NonDocentStore', return_value=store), patch.object(
                service, '_parse_document', side_effect=RuntimeError('synthetic_parse_failure')):
            result = service.parse_all_documents()
        self.assertEqual(result[0]['status'], 'failed')
        self.assertEqual(store.connection.rollback.call_count, 2)
        store.mark_document_parsed.assert_not_called()
        store.close.assert_called_once()

import unittest
from unittest.mock import patch

from app.services.parse_selection import reason, selective_live_selection
from app.services.selective_live_parser import parse_selected


class SelectiveLiveTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'document_id': 1, 'document_version_id': 2, 'sha256': 'a' * 64}
        self.item = dict(self.entry, doc_family='offered_positions', latest_run_id=None,
                         parsed_at=None, ingest_intent='live_new', detection_intent='live_new',
                         corrected_url=False, delivery_hold_reason=None)

    def test_selection_keeps_existing_gates(self):
        selection = selective_live_selection([self.entry])
        self.assertIsNone(reason(self.item, 'p', 'v', selection))
        for field, value in [('document_id', 9), ('document_version_id', 3),
                             ('sha256', 'b' * 64), ('ingest_intent', 'unknown'),
                             ('detection_intent', 'historical_import'),
                             ('latest_run_id', 10), ('corrected_url', True)]:
            changed = dict(self.item, **{field: value})
            self.assertIsNotNone(reason(changed, 'p', 'v', selection))

    def test_empty_and_duplicate_selections_fail(self):
        for entries in ([], [self.entry, self.entry]):
            with self.assertRaises(ValueError):
                selective_live_selection(entries)

    def test_selective_entrypoint_never_calls_matcher_or_reconciliation(self):
        names = ['OfferedPositionsParserService', 'FinalAwardListingMaestrosParserService',
                 'FinalAwardListingSecundariaParserService', 'DifficultCoverageProvisionalParserService',
                 'DocentBagParticipantsParserService', 'NonDocentParserService']
        with patch('app.services.assignment_matcher.AssignmentMatcherService.match_all', side_effect=AssertionError), \
             patch('app.services.notification_eligibility.reconcile_if_configured', side_effect=AssertionError):
            patches = [patch('app.services.selective_live_parser.' + name) for name in names]
            mocks = [p.start() for p in patches]
            try:
                for mock in mocks:
                    mock.return_value.parse_all_documents.return_value = []
                self.assertEqual(parse_selected([self.entry]), {'processed': 0, 'failed': 0})
            finally:
                for p in reversed(patches):
                    p.stop()


if __name__ == '__main__':
    unittest.main()

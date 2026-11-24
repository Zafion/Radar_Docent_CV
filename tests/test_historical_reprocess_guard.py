import unittest
from unittest.mock import patch

from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from scripts.reprocess_historical_isolated import NonEmptyBagParser, CheckedSecondaryParser
from types import SimpleNamespace


class HistoricalBagGuardTests(unittest.TestCase):
    def test_empty_extraction_fails_before_parser_can_replace_results(self):
        with patch.object(DocentBagParticipantsParserService, '_parse_document', return_value=[]):
            with self.assertRaisesRegex(ValueError, 'empty_extraction_rejected'):
                NonEmptyBagParser()._parse_document({})

    def test_nonempty_extraction_passes_through(self):
        rows = [object()]
        with patch.object(DocentBagParticipantsParserService, '_parse_document', return_value=rows):
            self.assertIs(NonEmptyBagParser()._parse_document({}), rows)

    def test_secondary_missing_status_is_rejected_before_replacement(self):
        rows = [SimpleNamespace(status='No adjudicat')]
        with patch.object(FinalAwardListingSecundariaParserService, '_parse_document', return_value=rows), \
             patch.object(CheckedSecondaryParser, '_resolve_file_path', return_value='fixture'), \
             patch.object(CheckedSecondaryParser, '_extract_clean_lines', return_value=['No adjudicat', 'No adjudicat']), \
             patch('scripts.reprocess_historical_isolated.is_career_layout', return_value=False):
            with self.assertRaisesRegex(ValueError, 'secondary_status_counts_mismatch'):
                CheckedSecondaryParser()._parse_document({'file_path':'fixture'})

    def test_secondary_matching_statuses_are_accepted(self):
        rows = [SimpleNamespace(status='No adjudicat')]
        with patch.object(FinalAwardListingSecundariaParserService, '_parse_document', return_value=rows), \
             patch.object(CheckedSecondaryParser, '_resolve_file_path', return_value='fixture'), \
             patch.object(CheckedSecondaryParser, '_extract_clean_lines', return_value=['No adjudicat']), \
             patch('scripts.reprocess_historical_isolated.is_career_layout', return_value=False):
            self.assertIs(CheckedSecondaryParser()._parse_document({'file_path':'fixture'}), rows)


if __name__ == '__main__':
    unittest.main()

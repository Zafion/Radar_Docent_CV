import unittest
from unittest.mock import patch

from app.services.final_award_listing_secundaria_parser import (
    FinalAwardListingSecundariaParserService, SecundariaExtractionValidationError,
)


class SecondaryCompletenessTests(unittest.TestCase):
    def test_order_only_non_award_gets_person_from_own_petition_not_assignment(self):
        lines = ['59/2', 'VACANT', 'Persona, Ejemplo Petición: 4', 'No adjudicat',
                 '416/60', 'VACANT', 'Otra, Persona Petición: 6', 'No adjudicat']
        service = FinalAwardListingSecundariaParserService()
        with patch.object(service, '_extract_clean_lines', return_value=lines):
            rows = service._parse_document({'file_path': '/fixture'})
        self.assertEqual([(r.order_number, r.person_display_name, r.status, r.assignment) for r in rows],
                         [(59, 'Persona, Ejemplo', 'No adjudicat', None),
                          (416, 'Otra, Persona', 'No adjudicat', None)])

    def test_incomplete_document_is_rejected(self):
        lines = ['59/2', 'VACANT', 'No adjudicat']
        service = FinalAwardListingSecundariaParserService()
        with patch.object(service, '_extract_clean_lines', return_value=lines):
            with self.assertRaises(SecundariaExtractionValidationError):
                service._parse_document({'file_path': '/fixture'})


if __name__ == '__main__':
    unittest.main()

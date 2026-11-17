import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from app.services.document_registry import DocumentRegistryService


class SelectedRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'fixture.pdf'
        self.path.write_bytes(b'synthetic')
        self.sha = hashlib.sha256(b'synthetic').hexdigest()
        self.entry = {'asset_id': 1, 'document_version_id': 2, 'sha256': self.sha,
                      'canonical_url': 'https://example.invalid/fixture', 'source_key': 'synthetic'}
        self.row = dict(self.entry, file_path=str(self.path), notification_ingest_intent='live_new',
                        original_filename='fixture.pdf', asset_title='fixture', asset_role='document',
                        source_label='synthetic', section=None, publication_label=None,
                        publication_date_text='17/11/2026', source_id=4)
        self.store = Mock()
        self.store.list_selected_unregistered_candidates.return_value = [self.row]
        self.store.create_document.return_value = 7
        self.service = DocumentRegistryService()
        self.service.classifier = Mock()
        self.service.classifier.classify.return_value = Mock(
            doc_family='offered_positions', title='fixture', document_date_text=None,
            document_date_iso=None, list_scope=None, classifier_version='test')

    def test_exact_selection_and_no_notification_queue(self):
        with patch('app.services.document_registry.DocumentStore', return_value=self.store), \
             patch('app.services.document_registry.register_detection') as detection:
            result = self.service.register_selected_live([self.entry])
        self.store.list_selected_unregistered_candidates.assert_called_once_with([1])
        self.assertEqual(result[0]['document_id'], 7)
        detection.assert_called_once()
        self.store.connection.commit.assert_called_once()

    def test_mismatch_rolls_back_without_registration(self):
        self.row['canonical_url'] = 'https://example.invalid/other'
        with patch('app.services.document_registry.DocumentStore', return_value=self.store):
            with self.assertRaisesRegex(ValueError, 'identity_mismatch'):
                self.service.register_selected_live([self.entry])
        self.store.create_document.assert_not_called()
        self.store.connection.rollback.assert_called_once()

    def test_existing_or_replayed_selection_fails_closed(self):
        self.store.list_selected_unregistered_candidates.return_value = []
        with patch('app.services.document_registry.DocumentStore', return_value=self.store):
            with self.assertRaisesRegex(ValueError, 'stale_or_registered'):
                self.service.register_selected_live([self.entry])
        self.store.connection.commit.assert_not_called()


if __name__ == '__main__':
    unittest.main()

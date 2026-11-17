"""Synthetic regressions for the actual ADC 174/26 and 177/27 layouts."""
import unittest
from unittest.mock import Mock, patch

from app.services.non_docent_parser import NonDocentParserService


def award(bag, code='123'):
    return f'4-E. {bag} 51.95 AE APELLIDO SINTETICO NOMBRE No {code} - CENTRO SINTETICO'


class NonDocentStaffGroupTests(unittest.TestCase):
    def parse(self, text, source):
        service = NonDocentParserService()
        store = Mock()
        store.get_staff_group_id_by_code.side_effect = {'EEI': 1, 'TOC': 2}.get
        store.upsert_publication.return_value = 10
        document = dict(file_path='synthetic.pdf', document_id=1,
                        doc_family='non_docent_adc_award', title='ADC sintético',
                        original_filename='synthetic.pdf', source_key=source)
        with patch.object(service, '_extract_pages_text', return_value=[text]):
            result = service._parse_document(store, document)
        return (store.upsert_publication.call_args.kwargs['staff_group_id'],
                [call.kwargs['staff_group_id'] for call in store.insert_award.call_args_list],
                result.rows_extracted)

    def test_adc_174_infantil_is_eei_with_three_digit_position(self):
        self.assertEqual(self.parse(award('Cos C1-04 Educació Infantil (OOP-18). Estabilització'),
                                    'non_docent_adc_eei'), (1, [1], 1))

    def test_adc_177_occupational_therapy_is_toc(self):
        self.assertEqual(self.parse(award('Teràpia Ocupacional'), 'non_docent_adc_toc'), (2, [2], 1))

    def test_two_rows_in_one_collective_keep_their_group(self):
        text = award('Educació Infantil') + ' ' + award('Educadors infantils', '456')
        self.assertEqual(self.parse(text, 'non_docent_adc_eei'), (1, [1, 1], 2))

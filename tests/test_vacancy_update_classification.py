"""Recognize the specific vacancy update filename with minimal metadata."""
import unittest
from unittest.mock import patch
from app.services.document_classifier import DocumentClassifierService


class VacancyUpdateClassificationTests(unittest.TestCase):
    def classify(self, filename, preview=''):
        c = DocumentClassifierService()
        with patch.object(c, '_read_pdf_preview', return_value=preview):
            return c.classify(file_path='synthetic.pdf', original_filename=filename,
                              asset_title='PDF', asset_role='pdf_candidate', source_key='rrhh_main',
                              source_label='RRHH', section=None, publication_label=None,
                              publication_date_text=None)

    def test_current_and_historical_course_names_with_or_without_storage_hash(self):
        for filename in ('lis_vac_act_adj_ini_26_27.pdf', 'lis_vac_act_adj_ini_25_26.pdf',
                         'LIS_VAC_ACT_ADJ_INI_26_27.PDF', 'lis_vac_act_adj_ini_26_27__abcdef123456.pdf'):
            with self.subTest(filename=filename):
                result=self.classify(filename)
                self.assertEqual((result.doc_family,result.list_scope),('offered_positions','inicio_curso'))
                self.assertIn('vacancy_update',result.signals)

    def test_unrelated_vacancy_files_are_not_forced_into_offered_positions(self):
        for filename in ('resol_vacantes_objeto_provision_26_27.pdf', 'vacantes_26_27.pdf',
                         'lis_vac_act_adj_ini_26_27.pdf.html', 'lis_vac_act_adj_ini_notas.pdf'):
            with self.subTest(filename=filename):
                self.assertNotEqual(self.classify(filename).doc_family, 'offered_positions')

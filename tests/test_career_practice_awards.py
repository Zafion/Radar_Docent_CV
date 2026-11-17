"""Carrera/prácticas: destinos anteriores al nombre, datos sintéticos."""
import unittest
from unittest.mock import patch
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from app.services.document_classifier import DocumentClassifierService


class CareerPracticeTests(unittest.TestCase):
    def parse(self, lines, secondary=False):
        parser = (FinalAwardListingSecundariaParserService() if secondary
                  else FinalAwardListingMaestrosParserService())
        with patch.object(parser, '_extract_clean_lines', return_value=lines):
            return parser._parse_document({'file_path': 'synthetic.pdf'})

    def test_2026_targets_belong_to_following_person_in_both_bodies(self):
        lines = ['MESTRES', 'EDUCACIÓ FÍSICA (114)', 'Ordre Cognoms i Nom Col·lectiu',
                 '00000001 CENTRO UNO - LOCALIDAD UNO',
                 'Adjudicat per la petició número 2. Lloc: 123456 128 EDUCACIÓN PRIMARIA',
                 '1 PERSONA SINTÉTICA, ALFAESTABILITZATS',
                 '00000002 CENTRO DOS - LOCALIDAD DOS',
                 "Adjudicat d'ofici província preferent Lloc: 234567 123 EDUCACIÓN FÍSICA",
                 '2 OTRA PERSONA, BETA PROVISIONALS']
        for secondary in (False, True):
            with self.subTest(secondary=secondary):
                rows = self.parse(lines, secondary)
                self.assertEqual([r.person_display_name for r in rows], ['PERSONA SINTÉTICA, ALFA', 'OTRA PERSONA, BETA'])
                self.assertEqual([r.assignment.position_code for r in rows], ['123456', '234567'])
                self.assertEqual([r.assignment.center_code for r in rows], ['00000001', '00000002'])
                self.assertEqual(rows[0].specialty_code, '114')
                self.assertEqual(rows[0].assignment.position_specialty_code, '128')
                self.assertEqual(rows[0].assignment.petition_number, 2)
                self.assertIsNone(rows[1].assignment.petition_number)
                self.assertIsNone(rows[0].assignment.assignment_kind)

    def test_2025_award_and_no_award_are_kept(self):
        rows = self.parse(['EDUCACIÓ FÍSICA (114)', 'Ordre Cognoms i Nom Col·lectiu',
            '00000001 CENTRO UNO - LOCALIDAD UNO',
            'Adjudicat per la petició número 2. Lloc: 123456 (Itinerant)',
            'PERSONA SINTÉTICA, ALFA', '128 EDUCACIÓN PRIMARIA', '1 PROVISIONALS',
            '--', 'No adjudicat.', 'OTRA PERSONA, BETA', '--', '2 REINGRESSATS'])
        self.assertEqual([r.status for r in rows], ['Adjudicat', 'No adjudicat'])
        self.assertEqual(rows[0].person_display_name, 'PERSONA SINTÉTICA, ALFA')
        self.assertEqual(rows[1].person_display_name, 'OTRA PERSONA, BETA')
        self.assertIsNone(rows[1].assignment)

    def test_result_after_wrapped_locality_and_before_name(self):
        rows = self.parse(['Ordre Cognoms i Nom Col·lectiu', '00000001 CENTRO UNO -',
            'LOCALIDAD UNOAdjudicat per la petició número 1. Lloc: 123456',
            '128 EDUCACIÓN PRIMARIA', '1 PERSONA SINTÉTICA, ALFA REINGRESSATS'])
        self.assertEqual(rows[0].assignment.locality, 'LOCALIDAD UNO')
        self.assertEqual(rows[0].assignment.center_name, 'CENTRO UNO')

    def test_specialty_restart_keeps_both_results(self):
        rows = self.parse(['LLENGUA CASTELLANA (105)', 'Ordre Cognoms i Nom Col·lectiu',
            '--', 'No adjudicat.', '1 PERSONA SINTÉTICA, ALFA REINGRESSATS',
            'LLENGUA VALENCIANA (107)', 'Ordre Cognoms i Nom Col·lectiu',
            '--', 'No adjudicat.', '1 PERSONA SINTÉTICA, ALFA REINGRESSATS'])
        self.assertEqual([r.order_number for r in rows], [1, 1])
        self.assertEqual([r.specialty_code for r in rows], ['105', '107'])

    def test_truncated_record_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'career_award_validation_failed'):
            self.parse(['Ordre Cognoms i Nom Col·lectiu', '00000001 CENTRO UNO - LOCALIDAD UNO',
                'Adjudicat per la petició número 1. Lloc: 123456'])

    def test_incomplete_destination_is_not_success(self):
        with self.assertRaisesRegex(ValueError, 'career_award_validation_failed') as error:
            self.parse(['Ordre Cognoms i Nom Col·lectiu', 'Adjudicat per la petició número 1. Lloc: 123456',
                '1 PERSONA SINTÉTICA, ALFA PROVISIONALS'])
        self.assertNotIn('PERSONA SINTÉTICA', str(error.exception))

    def test_real_minimal_metadata_classifies_both_current_documents(self):
        classifier = DocumentClassifierService()
        for filename, scope in [('ini_2026_adj_res_mae.pdf', 'maestros'),
                                ('ini_2026_adj_rpp_sec.pdf', 'secundaria_otros')]:
            with self.subTest(filename=filename), patch.object(classifier, '_read_pdf_preview', return_value='RESOLUCIÓ DEFINITIVA'):
                result = classifier.classify(file_path='synthetic.pdf', original_filename=filename,
                    asset_title='Listado', asset_role='pdf_candidate', source_key='family_adjudicacion',
                    source_label='Familia adjudicacion', section='Adjudicación', publication_label='Navegación', publication_date_text='15/07/2026')
                self.assertEqual((result.doc_family, result.list_scope), ('final_award_listing', scope))

    def test_closing_row_with_next_heading_on_same_line(self):
        rows = self.parse(['MESTRES', 'EDUCACIÓ FÍSICA (114)', 'Ordre Cognoms i Nom Col·lectiu',
            '--', 'No adjudicat.', 'PERSONA SINTÉTICA, ALFA',
            '1 PRÀCTIQUES LLENGUA VALENCIANA (107)',
            '--', 'No adjudicat.', 'OTRA PERSONA, BETA', '1 REINGRESSATS'])
        self.assertEqual([row.specialty_code for row in rows], ['114', '107'])

    def test_no_aptes_collective_does_not_merge_two_people(self):
        rows = self.parse(['Ordre Cognoms i Nom Col·lectiu', '--', 'No adjudicat.',
            '1 PERSONA SINTÉTICA, ALFA NO APTES', '--', 'No adjudicat.',
            '2 OTRA PERSONA, BETA ESTABILITZATS'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].person_display_name, 'PERSONA SINTÉTICA, ALFA')

    def test_wrapped_person_name_is_not_destination_specialty(self):
        rows = self.parse(['Ordre Cognoms i Nom Col·lectiu',
            '00000001 CENTRO UNO - LOCALIDAD UNO',
            'Adjudicat per la petició número 1. Lloc: 123456 (Mixta)',
            'PERSONA SINTÉTICA, ALFA DE LOS', 'NOMBRES',
            '219 TECNOLOGÍA', '1 PROVISIONALS'], secondary=True)
        self.assertEqual(rows[0].person_display_name, 'PERSONA SINTÉTICA, ALFA DE LOS NOMBRES')
        self.assertEqual(rows[0].assignment.position_specialty_code, '219')

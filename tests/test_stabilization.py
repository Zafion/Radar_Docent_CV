"""Contratos de estabilización: sin red, base real ni datos personales."""
import unittest
from app.api.main import build_user_view

PERSON={'display_name':'PERSONA SINTÉTICA'}
def award(day,status='Adjudicat',**context):
    return {'document_date_iso':day,'status':status,'assignments_count':1 if status=='Adjudicat' else 0,
            'assignments':[{'position_code':'123456'}] if status=='Adjudicat' else [],'publication_date_quality':'official',**context}

class PersonSummaryTests(unittest.TestCase):
    def test_document_date_is_not_publication_date_when_quality_unknown(self):
        result=build_user_view(PERSON,[award('2026-11-03',publication_date_quality='unknown')],[])
        self.assertEqual(result['current_result'],'no_data')
        self.assertEqual(result['current_result_label'],'Cronología no acreditada')
        self.assertIsNone(result['latest_date'])
        self.assertIsNone(result['assigned_position'])

    def test_missing_publication_quality_is_unverified(self):
        row=award('2026-11-03')
        del row['publication_date_quality']
        self.assertEqual(build_user_view(PERSON,[row],[])['current_result'],'no_data')

    def test_input_order_does_not_override_chronology(self):
        result=build_user_view(PERSON,[award('2025-09-01'),award('2026-11-01','Desactivat')],[])
        self.assertEqual(result['current_result'],'deactivated')
        self.assertIsNone(result['assigned_position'])

    def test_later_bag_does_not_reuse_historical_assignment(self):
        result=build_user_view(PERSON,[award('2025-09-01')],[],[{'document_date_iso':'2026-07-01','course_year':'2026/2027','order_number':7,'staff_kind':'interino','publication_date_quality':'official'}])
        self.assertEqual(result['current_result'],'docent_bag_member')
        self.assertIsNone(result['assigned_position'])

    def test_later_difficult_result_does_not_reuse_old_selected(self):
        result=build_user_view(PERSON,[],[{'document_date_iso':'2026-11-01','is_selected':False,'publication_date_quality':'official'},
                                         {'document_date_iso':'2025-09-01','is_selected':True,'position_code':'123456','publication_date_quality':'official'}])
        self.assertEqual(result['current_result'],'difficult_coverage_candidate')
        self.assertIsNone(result['assigned_position'])

    def test_same_date_different_scopes_is_neutral(self):
        result=build_user_view(PERSON,[award('2026-11-01',list_scope='maestros'),
                                       award('2026-11-01','Desactivat',list_scope='secundaria_otros')],[])
        self.assertEqual(result['current_result'],'ambiguous')
        self.assertIsNone(result['assigned_position'])

    def test_missing_date_cannot_establish_current_result(self):
        self.assertEqual(build_user_view(PERSON,[award(None)],[])['current_result'],'no_data')

    def test_later_correction_of_old_course_is_not_current_course(self):
        result=build_user_view(PERSON,[award('2026-11-04',course_year='2025/2026'),
                                       award('2026-11-03','Desactivat',course_year='2026/2027')],[])
        self.assertEqual(result['current_result'],'no_data')

    def test_award_wording_is_limited_to_publication(self):
        result=build_user_view(PERSON,[award('2025-09-01',procedure='inicio_curso',list_scope='maestros')],[])
        self.assertNotIn('Sí tienes',result['current_result_message'])
        self.assertIn('publicación',result['current_result_message'].lower())

    def test_same_date_different_procedures_is_neutral(self):
        result = build_user_view(PERSON, [
            award('2026-11-01', procedure='inicio_curso'),
            award('2026-11-01', 'No adjudicat', procedure='continua'),
        ], [])
        self.assertEqual(result['current_result'], 'ambiguous')
        self.assertIsNone(result['assigned_position'])

    def test_new_procedure_does_not_inherit_old_assignment(self):
        result = build_user_view(PERSON, [
            award('2026-07-15', procedure='inicio_curso', course_year='2026/2027'),
            award('2026-11-01', 'No adjudicat', procedure='continua', course_year='2026/2027'),
        ], [])
        self.assertEqual(result['current_result'], 'not_awarded')
        self.assertEqual(result['result_context']['procedure'], 'continua')
        self.assertIsNone(result['assigned_position'])

    def test_filename_identifies_old_course_correction(self):
        result = build_user_view(PERSON, [
            award('2026-11-04', original_filename='ini_2025_adj_int_mae.pdf'),
            award('2026-11-03', 'Desactivat', original_filename='ini_2026_adj_int_mae.pdf'),
        ], [])
        self.assertEqual(result['current_result'], 'no_data')

    def test_invalid_date_is_neutral(self):
        self.assertEqual(build_user_view(PERSON, [award('2026-99-01')], [])['current_result'], 'no_data')

    def test_missing_assignments_is_safe(self):
        result = build_user_view(PERSON, [{'document_date_iso': '2026-11-01',
            'status': 'Adjudicat', 'assignments_count': 1}], [])
        self.assertIsNone(result['assigned_position'])

import unittest
from scripts.certify_historical_404_674 import evaluate


def valid_fixture():
    fields={k:{'current_only':0,'prior_only':0} for k in ('documents','results','assignments','offers','bag','difficult_positions','difficult_candidates','nov19_quarantine_core')}
    return {'previous':{'status':'reconciled_with_prior_differences','pending':[]},
      'pdf_hashes':{'404':'acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0','674':'823d06203fa90d2bafefc250793dfafa7141a2b1e1dd3ed2e1ce8a506f9f9793'},
      'trial':{'documents':{'404':{'status':'applied','first':{'bag_members':465,'ambiguous_rows':16}},'674':{'status':'applied','first':{'difficult_positions':52,'difficult_candidates':5381}}},'rollback_674':'PASS','unvalidated_constraints':0},
      'identity':{'status':'DOCUMENT_IDENTITIES_VERIFIED','404':{'reused':465,'quarantined':16,'inserted':0},'674':{'positions_reused':52,'candidates_reused':5381,'candidates_inserted':0}},
      'preservation':{'status':'PRESERVATION_PASS','notification_contents_preserved':True,'tables':34},
      'quarantine':{'status':'QUARANTINES_PASS','q439_preserved':True,'q439':8,'q404':16,'q19':598,'version674_availability':[{'availability_status':'unknown','n':52}]},
      'rollback404':{'status':'ROLLBACK_404_PASS'},
      'mixed':{'status':'MIXED_REAL_PASS','first404':{'actions':{'inserted':1}},'first674':{'actions':{'inserted':1}},'second404':{'actions':{}},'second674':{'actions':{}}},
      'comparison':{'field_multisets':fields,'procedure_relations':{'current_only':0,'prior_only':0}},
      'prior_notifications':{'status':'NOTIFICATIONS_COMPARED','tables':{k:{'identical':n,'current_only':0,'prior_only':0} for k,n in (('public_alerts',194),('notification_posts',776),('push_notification_events',204))}},
      'metadata':{'status':'QUARANTINE_METADATA_PROFILE','prior':{'availability_status':{'values':[{'value':'available','n':598}]},**{k:{'json_null':598} for k in ('availability_checked_at','availability_reason','closed_at','closed_by_document_id')}},'current':{k:{'missing':598} for k in ('availability_checked_at','availability_reason','availability_status','closed_at','closed_by_document_id')}},
      'regression':{'exit_code':0,'tests_sha256':{'fixture':'sha'}},'test_hashes':{'fixture':'sha'}}


class ReceiptTests(unittest.TestCase):
    def test_with_documentary_limitations(self):self.assertEqual(evaluate(valid_fixture())['status'],'reconciled_with_documentary_limits')
    def test_missing_or_different_is_partial(self):
        x=valid_fixture();del x['identity'];self.assertEqual(evaluate(x)['status'],'partial')
        x=valid_fixture();x['comparison']['field_multisets']['bag']['prior_only']=1
        self.assertIn('previous_fields_bag',evaluate(x)['pending'])
        x=valid_fixture();x['quarantine']['q404']=15
        self.assertIn('quarantines',evaluate(x)['pending'])

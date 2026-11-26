import unittest
from scripts.certify_historical_439 import evaluate


def fixture():
    first={'source_rows':3,'source_assignments':3,'inserted':2,'assignments_inserted':2,'quarantined':1}
    second={'inserted':0,'assignments_inserted':0,'reused':2,'quarantined':1}
    fields={k:{'identical':2,'current_only':0,'prior_only':0} for k in ('documents','results','assignments','offers','nov19_quarantine_core')}
    return {'trial':{'sha256':'2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4','first':first,'second':second,
      'before':{'documents':1,'award_results':5,'award_assignments':5},'after':{'documents':1,'award_results':7,'award_assignments':7},'rollback':'PASS','unvalidated_constraints':0},
      'preservation':{'status':'PRESERVATION_PASS','notification_contents_preserved':True,'tables':34,'quarantines':{'recovery_quarantine.positions_19nov':{'count':598,'content_equal':True},'recovery_unawarded.quarantine':{'count':2548,'content_equal':True}}},
      'evidence':{'status':'DOCUMENT_EVIDENCE_REGISTERED','first':{'quarantined':1},'second':{'inserted':0}},
      'previous':{'version_439':{'identical_record_and_assignment_multiset':2,'current_only':0,'prior_only':0},'documents':{'common':1,'current_only':0,'prior_only':0},'field_multisets':fields,'procedure_relations':{'current_only':0,'prior_only':0}},
      'source_certificate':{'status':'complete'}}


class Version439ReceiptTests(unittest.TestCase):
    def test_complete(self):self.assertEqual(evaluate(fixture())['status'],'complete')
    def test_missing_and_conflict(self):
        x=fixture();del x['trial'];self.assertEqual(evaluate(x)['status'],'partial')
        x=fixture();x['previous']['version_439']['current_only']=1;self.assertIn('previous_439_identity',evaluate(x)['pending'])
    def test_prior_difference_is_explicit(self):
        x=fixture();x['previous']['field_multisets']['bag']={'identical':5,'current_only':0,'prior_only':2}
        self.assertEqual(evaluate(x)['status'],'reconciled_with_prior_differences')

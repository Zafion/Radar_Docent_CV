import copy
import unittest
from scripts.certify_cumulative_receipt import evaluate


def valid_fixture():
    cluster='/fixture/cluster'; keys=('700','436','701','expert','1000067','1000444')
    docs={k:{'status':'applied','result':{}} for k in keys}
    first=copy.deepcopy(docs)
    for k,(r,a) in {'700':(6314,90),'436':(14439,2569),'701':(2,0),'1000067':(6028,124),'1000444':(10659,111)}.items():first[k]['result']={'inserted':r,'awards_inserted':a}
    first['expert']['result']={'vacancies_inserted':6}
    hashes={k:k+'-sha' for k in ('700','436','701','1000391','1000067','1000444')}
    counts={'700':(6416,93),'436':(17051,5116),'701':(11024,120),'1000067':(6028,124),'1000444':(10659,111),'1000391':(0,0)}
    observed={k:{'sha256':hashes[k],'documents':1,'results':r,'assignments':a} for k,(r,a) in counts.items()}
    tables=('documents','document_versions','award_results','award_assignments','offered_positions','centers','docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates','public_alerts','notification_posts','push_notification_events')
    sha='4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b'
    return {'baseline':{'status':'BASELINE_RESTORED','source_sha256':sha,'work_directory':cluster},
      'sources':{'status':'SOURCES_PREPARED','source_document_identities':9,'source_award_results':10,'cluster':cluster},
      'hash_checks':{'baseline':sha,'reconstruction':'b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a','documents':hashes,'receipt_names':{'quarantine':'first.json','replay':'second.json'},'synthetic_test_sha256':'test-hash'},
      'first':{'report':{'documents':first}},'quarantine':{'report':{'quarantine':[{'result':{'inserted':299}},{'result':{'inserted':299}}]}},
      'replay':{'report':{'documents':copy.deepcopy(docs),'quarantine':[{'result':{'inserted':0,'reused':299}},{'result':{'inserted':0,'reused':299}}]}},
      'execution':{'status':'COORDINATOR_REPLAY_CHECK','counts_idempotent':True,'cluster':cluster,'first':{'receipt':'/work/first.json'},'second':{'receipt':'/work/second.json'}},
      'verification':{'status':'VERIFY_PASS','cluster':cluster,'matches_700':93,'matches_701':120,'matches_19nov':0,'offer_quarantine_rows':598,'rollback':'PASS','unvalidated_constraints':0,
          'procedure_documents':[{'procedure_key':k,'n':v} for k,v in (('interino-2026-11-03',3),('expertos-2026-11-05',1),('interino-2026-11-19',4))],
          'review_quarantine':[{'reason':'raw_block_conflict_unresolved','n':1},{'reason':'target_association_without_source_evidence','n':2547}]},
      'observation':{'status':'OBSERVATION_COMPLETE','cluster':cluster,'document_identities':{'n':778,'sha_count':778,'missing_sha':0},'documents':observed,
          'baseline_tables':list(tables),'baseline_tables_without_primary_key':[],
          'baseline_comparisons':{t:{'baseline_rows_missing':0,'baseline_rows_changed':0} for t in tables},
          'totals':{'documents':778,'award_results':593050,'award_assignments':47966,'public_alerts':194,'notification_posts':776,'push_notification_events':204},'experts':{'n':6,'unawarded':6}},
      'synthetic':{'suite':'tests.test_historical_cumulative_real_adapters','exit_code':0,'test_sha256':'test-hash'}}


class AggregateTests(unittest.TestCase):
    def test_complete_with_required_evidence(self):self.assertEqual(evaluate(valid_fixture())['status'],'complete')
    def test_missing_evidence_is_partial(self):
        fixture=valid_fixture();del fixture['replay'];self.assertEqual(evaluate(fixture)['status'],'partial')
    def test_discrepancy_and_replay_insertion_are_partial(self):
        fixture=valid_fixture();fixture['observation']['totals']['award_results']+=1
        self.assertIn('current_totals',evaluate(fixture)['pending'])
        fixture=valid_fixture();fixture['replay']['report']['documents']['700']['result']['inserted']=1
        self.assertIn('replay_insertions',evaluate(fixture)['pending'])

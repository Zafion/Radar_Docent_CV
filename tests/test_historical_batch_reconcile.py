import unittest
from unittest.mock import patch
from app.services import historical_batch_reconcile as service

class Query:
    def __init__(self,value):self.value=value
    def fetchone(self):return self.value
class New:
    def execute(self,sql,args):
        if 'document_versions' in sql:return Query({'sha256':'a'*64})
        return Query({'doc_family':'docent_bag_participants'})
class Destination:
    def execute(self,sql,args):return Query({'id':44,'source_url':'https://ceice.gva.es/es/web/rrhh-educacion'})

class BatchReconciliationTests(unittest.TestCase):
    def test_classification_never_equates_missing_or_duplicates(self):
        base={'baseline_rows':1,'reconstruction_rows':1,'unkeyed_baseline':0,'unkeyed_reconstruction':0,'categories':{}}
        cases=[(dict(base,categories={'identical':1}),'A'),(dict(base,baseline_rows=0),'B'),
          (dict(base,categories={'different_fields':1}),'C'),(dict(base,categories={'ambiguous_duplicate_key':1}),'E'),
          (dict(base,unkeyed_baseline=1),'E')]
        for data,expected in cases:
            with self.subTest(expected=expected),patch.object(service,'compare_document',return_value={'bag_members':data}):
                self.assertEqual(service.classify_document(None,None,'a'*64,'bag_members',b'x'*32)[0],expected)

    def test_new_document_uses_destination_ids_and_unknown_states(self):
        asset={'source_key':'rrhh_main','source_url':'https://ceice.gva.es/es/web/rrhh-educacion'}
        fields={'document_versions':['sha256'],'document_parse_runs':['document_version_id'],
                'documents':['doc_family','document_version_id','source_id','validation_status','validation_parse_run_id','historical_status','publication_date_quality']}
        new=New();dst=Destination();source={'id':10,'document_version_id':20};parse={'document_version_id':20}
        # Fields absent from the source fixture are represented as they would be in DB rows.
        orig=new.execute
        def query(sql,args):
            if 'FROM documents' in sql:return Query({'doc_family':'docent_bag_participants','document_version_id':20,'source_id':12,
              'validation_status':None,'validation_parse_run_id':None,'historical_status':None,'publication_date_quality':None})
            return orig(sql,args)
        new.execute=query
        with patch.object(service,'copy_fields',side_effect=lambda n,d,t:fields[t]),patch.object(service,'insert_row',side_effect=[900,901,902]) as insert:
            self.assertEqual(service.create_verified_document(new,dst,source,parse,asset),902)
            doc=insert.call_args_list[-1].args[2]
            self.assertEqual((doc['document_version_id'],doc['source_id'],doc['validation_parse_run_id']),(900,44,901))
            self.assertEqual((doc['historical_status'],doc['publication_date_quality']),('unknown','unknown'))
        with self.assertRaisesRegex(ValueError,'new_document_official_source_unavailable'):
            service.create_verified_document(new,dst,source,parse,{'source_url':'https://example.invalid'})

    def test_other_family_conflict_prevents_document_identity_claim(self):
        equal={'baseline_rows':1,'reconstruction_rows':1,'unkeyed_baseline':0,'unkeyed_reconstruction':0,'categories':{'identical':1}}
        other=dict(equal,categories={'different_fields':1})
        with patch.object(service,'compare_document',return_value={'difficult_candidates':equal,'difficult_positions':other}):
            category,data=service.classify_document(None,None,'a'*64,'difficult_candidates',b'x'*32)
        self.assertEqual(category,'C')
        self.assertEqual(data['other_families_with_conflicts'],['difficult_positions'])

    def test_new_candidate_family_accepts_its_missing_position_dependency(self):
        absent={'baseline_rows':0,'reconstruction_rows':1,'unkeyed_baseline':0,'unkeyed_reconstruction':0,'categories':{'reconstruction_only':1}}
        with patch.object(service,'compare_document',return_value={'difficult_candidates':dict(absent),'difficult_positions':dict(absent)}):
            category,_=service.classify_document(None,None,'a'*64,'difficult_candidates',b'x'*32)
        self.assertEqual(category,'B')

    def test_mixed_rows_add_only_new_and_keep_conflicts_and_duplicates(self):
        old=[{'source_id':1,'order':1,'value':None},{'source_id':2,'order':2,'value':'old'}]
        new=[{'source_id':11,'order':1,'value':False},{'source_id':12,'order':2,'value':'old'},
             {'source_id':13,'order':3,'value':'new'},{'source_id':14,'order':4,'value':'x'},
             {'source_id':15,'order':4,'value':'x'},{'source_id':16,'order':None,'value':'x'}]
        plan=service.plan_mixed_rows(old,new,['order'],['value']);added=[];quarantine=[]
        counts=service.apply_mixed_plan(plan,lambda ident,reason:added.append(ident),lambda ident,category,fields:quarantine.append((ident,category,fields)))
        self.assertEqual(added,[13]);self.assertEqual(counts,{'C':1,'A':1,'B':1,'E':3})
        self.assertIn((11,'C',['value']),quarantine)
        self.assertEqual({x[0] for x in quarantine},{11,14,15,16})

    def test_unkeyed_existing_rows_prevent_claiming_absence(self):
        plan=service.plan_mixed_rows([{'source_id':1,'order':None}],[{'source_id':2,'order':2}],['order'],[])
        self.assertEqual(plan['actions'][0]['category'],'E')

    def test_absent_field_is_not_equivalent_to_sql_null(self):
        plan=service.plan_mixed_rows([{'source_id':1,'order':1}],[{'source_id':2,'order':1,'value':None}],['order'],['value'])
        self.assertEqual(plan['actions'][0]['category'],'C')
        self.assertEqual(plan['actions'][0]['changed_fields'],['value'])

if __name__=='__main__':unittest.main()

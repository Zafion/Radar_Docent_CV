import unittest

from app.services.historical_row_comparison import compare_attested_rows


def row(number,fields,sha='a'*64,attested=True):
    return {'document_sha256':sha,'record_kind':'award','scope':'maestros.001',
            'source_ordinal':number,'locator_attested':attested,'fields':fields}


class HistoricalRowComparisonTests(unittest.TestCase):
    def test_same_document_only_and_no_sensitive_values_in_report(self):
        old=[row(1,{'name':'PRIVATE NAME','status':'no'}),row(2,{'status':'old'}),
             row(3,{'status':'same'}),row(4,{'status':'x'},attested=False)]
        new=[row(1,{'name':'PRIVATE NAME','status':'yes'}),row(3,{'status':'same'}),
             row(5,{'status':'new'}),row(1,{'name':'PRIVATE NAME','status':'yes'},sha='b'*64)]
        result=compare_attested_rows(old,new)
        self.assertEqual(result['counts'],{'different_fields':1,'only_baseline_snapshot':1,
            'identical':1,'only_reconstructed_snapshot':2})
        self.assertEqual(result['unkeyed_baseline_rows'],1)
        self.assertNotIn('PRIVATE NAME',str(result))
        self.assertEqual(result['details'][0]['changed_fields'],['status'])

    def test_duplicate_locator_never_becomes_a_match(self):
        result=compare_attested_rows([row(1,{'x':1}),row(1,{'x':1})],[row(1,{'x':1})])
        self.assertEqual(result['counts'],{'ambiguous_duplicate_locator':1})

    def test_invalid_locator_never_falls_back_to_person_name(self):
        record=row(1,{'name':'same'});record['scope']='SOME PERSON NAME'
        result=compare_attested_rows([record],[record])
        self.assertEqual(result['counts'],{})
        self.assertEqual((result['unkeyed_baseline_rows'],result['unkeyed_reconstructed_rows']),(1,1))


if __name__=='__main__':unittest.main()

import unittest
from app.services.historical_database_row_compare import compare_document,FAMILIES

class Cursor:
    def __init__(self,rows):self.rows=rows
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args):self.values=list(self.rows.get(sql,[]))
    def fetchmany(self,n):out=self.values[:n];self.values=self.values[n:];return out
class DB:
    def __init__(self,rows):self.rows=rows
    def cursor(self,name):return Cursor(self.rows)

class DatabaseRowComparisonTests(unittest.TestCase):
    def test_private_values_never_exported_and_conflicts_ambiguous(self):
        sql=FAMILIES['award_results'][0]
        old=[('maestros','01','001',1,'SECRET NAME','ok','s'),('maestros','01','001',2,'OLD PERSON','ok','s'),
             ('maestros','01','001',3,'x','ok','s'),('maestros','01','001',3,'x','ok','s'),
             ('maestros','01',None,4,'x','ok','s')]
        new=[('maestros','01','001',1,'SECRET NAME','ok','s'),('maestros','01','001',2,'NEW PERSON','ok','s'),
             ('maestros','01','001',3,'x','ok','s'),('maestros','01','001',5,'x','ok','s')]
        result=compare_document(DB({sql:old}),DB({sql:new}),'a'*64,b'x'*32)['award_results']
        self.assertEqual(result['categories'],{'identical':1,'ambiguous_person_conflict':1,
                         'ambiguous_duplicate_key':1,'reconstruction_only':1})
        self.assertEqual(result['unkeyed_baseline'],1)
        self.assertNotIn('SECRET NAME',str(result));self.assertNotIn('OLD PERSON',str(result))

if __name__=='__main__':unittest.main()

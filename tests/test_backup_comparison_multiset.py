import unittest
from scripts.compare_backup_contents import difference_counts


class Cursor:
    def __init__(self, rows): self.rows=iter(rows)
    def __enter__(self): return self
    def __exit__(self, *args): return None
    def execute(self, query): pass
    def fetchmany(self, count):
        result=[]
        for _ in range(count):
            try: result.append(next(self.rows))
            except StopIteration: break
        return result


class DB:
    def __init__(self, rows): self.rows=rows
    def cursor(self, name): return Cursor(self.rows)


class DifferenceCountsTests(unittest.TestCase):
    def test_duplicate_rows_preserve_multiplicity(self):
        a=DB([('a',),('a',),('b',)])
        b=DB([('a',),('b',),('b',)])
        self.assertEqual(difference_counts(a,b,'example',[]),
                         {'only_left':1,'only_right':1,'changed_same_primary_key':None})

    def test_keyed_changes(self):
        a=DB([('1','old'),('2','same')])
        b=DB([('1','new'),('3','added')])
        self.assertEqual(difference_counts(a,b,'example',['id']),
                         {'only_left':1,'only_right':1,'changed_same_primary_key':1})


if __name__=='__main__': unittest.main()

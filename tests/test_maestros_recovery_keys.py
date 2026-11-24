import unittest
from scripts.recover_maestros_baseline17 import plan_ordinals,plan_exact_blocks
from scripts.recover_seven_maestros import shared_ordinal_conflicts

class DocumentaryKeyTests(unittest.TestCase):
    def test_unique_ordinals_reuse_existing_and_add_only_absent(self):
        self.assertEqual(plan_ordinals([{'order_number':1},{'order_number':3}],[{'order_number':1},{'order_number':2},{'order_number':3}]),{2})

    def test_duplicate_or_missing_ordinal_stops(self):
        for old,new in (([{'order_number':1},{'order_number':1}],[{'order_number':1}]),
                        ([{'order_number':None}],[{'order_number':1}]),
                        ([{'order_number':2}],[{'order_number':1}])):
            with self.assertRaises(ValueError):plan_ordinals(old,new)

    def test_exact_blocks_preserve_source_duplicates(self):
        a=[{'raw_block_text':'a'},{'raw_block_text':'a'}]
        b=a+[{'raw_block_text':'b'}]
        self.assertEqual(len(plan_exact_blocks(a,b)),1)
        with self.assertRaises(ValueError):plan_exact_blocks(a,[{'raw_block_text':'a'}])
        with self.assertRaises(ValueError):plan_exact_blocks(a,b+[{'raw_block_text':'b'}])

    def test_shared_ordinal_conflict_is_detected_not_merged(self):
        old=[{'order_number':1,'person_name_normalized':'A'}]
        new=[{'order_number':1,'person_name_normalized':'B'},{'order_number':2,'person_name_normalized':'A'}]
        self.assertEqual(shared_ordinal_conflicts(old,new),1)

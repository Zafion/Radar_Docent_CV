"""DR-02 policy and read-only CLI boundaries; execution is exercised in PostgreSQL."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services.parse_selection import Selection, reason, selection_from_plan, verified_sha
from app.services.parse_plan import preview, execute_plan


class ParseSelectionTests(unittest.TestCase):
    def item(self, **changes):
        return dict(document_id=1, document_version_id=2, sha256='a'*64,
                    doc_family='offered_positions', latest_run_id=None, parsed_at=None,
                    ingest_intent='live_new', detection_intent='live_new', corrected_url=False,
                    delivery_hold_reason=None, **changes)

    def test_only_first_live_attempt_is_automatic(self):
        self.assertIsNone(reason(self.item(), 'p', 'new', Selection()))
        for intent in ('unknown', 'historical_import', 'reprocess'):
            item=self.item(); item['ingest_intent']=intent
            self.assertEqual(reason(item, 'p', 'new', Selection()), 'non_live_requires_plan')

    def test_previous_attempt_does_not_depend_on_parser_version_or_status(self):
        item=self.item(); item['latest_run_id']=3
        for key,version in [('old','old'),('new','new')]:
            self.assertEqual(reason(item,key,version,Selection()),'previous_attempt_requires_plan')

    def test_corrections_unknown_and_holds_are_not_automatic(self):
        for key,value in [('corrected_url',True),('delivery_hold_reason','held'),('doc_family','unknown')]:
            item=self.item(); item[key]=value
            self.assertIsNotNone(reason(item,'p','new',Selection()))

    def test_explicit_plan_bound_to_sha_version_parser_and_latest_attempt(self):
        entry=dict(document_id=1,document_version_id=2,sha256='a'*64,doc_family='offered_positions',
                   parser_key='p',parser_version='new',expected_latest_run_id=None)
        selection=selection_from_plan(dict(format='funkcionario.parse-plan.v1',mode='reprocess',entries=[entry]))
        self.assertIsNone(reason(self.item(),'p','new',selection))
        for key,value in [('sha256','b'*64),('latest_run_id',9),('document_version_id',99)]:
            item=self.item();item[key]=value
            self.assertIsNotNone(reason(item,'p','new',selection))
        self.assertIsNotNone(reason(self.item(),'p','changed',selection))

    def test_no_empty_or_generic_force_plan(self):
        for plan in ({},dict(format='funkcionario.parse-plan.v1',mode='reprocess',entries=[])):
            with self.assertRaises(ValueError):selection_from_plan(plan)
        with self.assertRaisesRegex(ValueError,'authorization'):execute_plan({},authorized=False)
        with self.assertRaisesRegex(ValueError,'sha_list'):preview(reprocess=True)

    def test_preview_starts_read_only_and_never_runs_writers(self):
        conn=Mock();conn.execute.return_value=SimpleNamespace(fetchall=lambda:[])
        with patch('app.storage.db.get_connection',return_value=conn):
            result=preview()
        self.assertEqual(conn.execute.call_args_list[0].args,('SET TRANSACTION READ ONLY',))
        self.assertTrue(result['read_only'])
        self.assertEqual(result['entries'],[])
        self.assertEqual(len(conn.execute.call_args_list),2)
        conn.commit.assert_not_called();conn.rollback.assert_called_once();conn.close.assert_called_once()

    def test_file_bytes_must_match_full_sha(self):
        with tempfile.TemporaryDirectory(dir='tests') as directory:
            path=Path(directory)/'synthetic';path.write_bytes(b'synthetic')
            verified_sha(path,hashlib.sha256(b'synthetic').hexdigest())
            with self.assertRaisesRegex(ValueError,'do_not_match'):verified_sha(path,'0'*64)

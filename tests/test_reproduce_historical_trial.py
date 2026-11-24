import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from scripts import reproduce_historical_trial as runner

class ReproductionTests(unittest.TestCase):
    def test_one_restore_then_six_ordered_steps(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);stdin=Mock();stdin.isatty.return_value=True;stdout=io.StringIO();stdout.isatty=lambda:True
            calls=[]
            def execute(args,**kwargs):
                calls.append((args,kwargs))
                if len(calls)==1:
                    session=root/'baseline_copy_synthetic';session.mkdir()
                    (session/'receipt.json').write_text(json.dumps({'status':'BASELINE_RESTORED','source_sha256':runner.SHA}))
                if args[-1]=='--batch-02':
                    (root/'reconciliacion_lote_02_20261123.json').write_text(json.dumps({'after':{'documents':1},'rollback_tested':True,'external_notifications':'unchanged','second_execution':{'before':{'documents':1},'after':{'documents':1}}}))
                return Mock(returncode=0)
            with patch.object(runner,'OUT',root),patch.object(runner,'preflight'),patch.object(runner,'_hash_file',return_value='a'*64),patch.object(runner.sys,'stdin',stdin),patch.object(runner.sys,'stdout',stdout),patch.object(runner.subprocess,'run',side_effect=execute):
                runner.run()
            self.assertEqual([c[0][-1] for c in calls[1:]],['--trial','--functional-03','--functional-0519','--batch-01','--batch-02','--duplicate-guard'])
            self.assertTrue(all(c[1]['env']['FUNK_BASELINE_RECEIPT'].endswith('/receipt.json') for c in calls[1:]))
            self.assertIn('REPRODUCED',stdout.getvalue())
            receipt=json.loads((root/'baseline_copy_synthetic/reproduction_receipt.json').read_text())
            self.assertEqual(receipt['status'],'REPRODUCED')
            self.assertEqual(receipt['final_counts'],{'documents':1})
            self.assertEqual(receipt['rollback'],'verified')

    def test_interruption_never_reports_success(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);stdin=Mock();stdin.isatty.return_value=True;stdout=io.StringIO();stdout.isatty=lambda:True
            def execute(args,**kwargs):
                if args[-1].endswith('copy_encrypted_baseline_manual.py'):
                    session=root/'baseline_copy_synthetic';session.mkdir()
                    (session/'receipt.json').write_text(json.dumps({'status':'BASELINE_RESTORED','source_sha256':runner.SHA}))
                else:raise KeyboardInterrupt()
            with patch.object(runner,'OUT',root),patch.object(runner,'preflight'),patch.object(runner,'_hash_file',return_value='a'*64),patch.object(runner.sys,'stdin',stdin),patch.object(runner.sys,'stdout',stdout),patch.object(runner.subprocess,'run',side_effect=execute):
                with self.assertRaises(KeyboardInterrupt):runner.run()
            receipt=json.loads((root/'baseline_copy_synthetic/reproduction_receipt.json').read_text())
            self.assertEqual(receipt['status'],'STOP')
            self.assertNotIn('REPRODUCED',stdout.getvalue())

if __name__=='__main__':unittest.main()

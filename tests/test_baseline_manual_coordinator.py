import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from scripts import copy_encrypted_baseline_manual as manual

class ManualCoordinatorTests(unittest.TestCase):
    def test_snapshot_uses_numeric_file_type_and_rejects_nonregular(self):
        fake_lab=Mock();fake_lab.stat.return_value.st_dev=123
        def row(mode):return f'123 261649 {manual.SIZE} 995 995 600 1 1 2 {mode}'.encode()
        with patch.object(manual,'LAB',fake_lab),patch.object(manual,'privileged',return_value=row('8180')) as call:
            self.assertEqual(manual.snapshot()[-1],'8180')
            self.assertIn('%f',call.call_args.args[1])
            self.assertNotIn('%F',call.call_args.args[1])
        with patch.object(manual,'LAB',fake_lab),patch.object(manual,'privileged',return_value=row('4180')):
            with self.assertRaisesRegex(ValueError,'dump_not_regular'):manual.snapshot()

    def test_one_sudo_authentication_and_fixed_read_commands(self):
        source=b'synthetic administrator flow';sha=hashlib.sha256(source).hexdigest()
        commit={'action':'COMMIT','run_id':manual.RUN,'sha256':sha,'source_exit_code':0,'prepared_validated':True}
        ack={'status':'COORDINATOR_DONE','ack':{'status':'PUBLISHED','size':len(source),'sha256':sha}}
        calls=[]
        def priv(*args,**kwargs):
            calls.append(args)
            return json.dumps(commit if Path(args[-1])==manual.COMMIT else ack).encode()
        proc=Mock();proc.stdout=io.BytesIO(source);proc.wait.return_value=0;proc.poll.return_value=0
        stdin=Mock();stdin.isatty.return_value=True;stdout=io.StringIO();stdout.isatty=lambda:True
        with tempfile.TemporaryDirectory() as td, patch.object(manual,'BASE',Path(td)), patch.object(manual,'SIZE',len(source)), \
             patch.object(manual.sys,'stdin',stdin), patch.object(manual.sys,'stdout',stdout), \
             patch.object(manual,'command',return_value=b'/dev/mapper/funkrec_lab_20261107 ext4 ro,nodev,nosuid,noexec,norecovery'), \
             patch.object(manual,'privileged',side_effect=priv),patch.object(manual,'snapshot',return_value=['stable']), \
             patch.object(manual,'digest',return_value=sha),patch.object(manual,'validate_volatile_storage'), \
             patch.object(manual.subprocess,'run',return_value=Mock(returncode=0)) as sudo, \
             patch.object(manual.subprocess,'Popen',return_value=proc) as copy, \
             patch.object(manual,'restore_ciphertext',return_value=(Path('/dev/shm/synthetic'),{'status':'RESTORED'})) as restore:
            manual.main()
            self.assertEqual(sudo.call_count,1)
            self.assertEqual(sudo.call_args.args[0],['/usr/bin/sudo','-v'])
            self.assertEqual(copy.call_args.args[0][:4],['/usr/bin/sudo','-n','--','/usr/bin/cat'])
            self.assertTrue(all(args[:2]==('/usr/bin/cat','--') for args in calls))
            self.assertEqual(restore.call_count,1)
            self.assertIn('BASELINE_RESTORED',stdout.getvalue())

if __name__=='__main__':unittest.main()

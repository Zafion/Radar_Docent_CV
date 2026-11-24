import os,subprocess,tempfile,unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class RecoveryStartTests(unittest.TestCase):
    def test_defaults_override_unsafe_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binary=root/'.venv/bin/python';binary.parent.mkdir(parents=True)
            binary.write_text('#!/bin/sh\nprintf "%s:%s\\n" "$FUNK_RECOVERY_READ_ONLY" "$ENABLE_NOTIFICATIONS"\n')
            binary.chmod(0o700)
            env={'PATH':'/usr/bin:/bin','FUNK_RECOVERY_READ_ONLY':'0','ENABLE_NOTIFICATIONS':'1','RADAR_DOCENT_DB_URL':'synthetic'}
            result=subprocess.run(['/bin/bash',str(ROOT/'scripts/start_recovery_readonly.sh')],cwd=root,env=env,capture_output=True,text=True,check=True)
            self.assertEqual(result.stdout.strip(),'1:0')

    def test_missing_database_fails_before_launch(self):
        result=subprocess.run(['/bin/bash',str(ROOT/'scripts/start_recovery_readonly.sh')],cwd=ROOT,env={'PATH':'/usr/bin:/bin'},capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)

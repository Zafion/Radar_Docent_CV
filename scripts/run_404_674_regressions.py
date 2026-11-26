"""Record isolated 404/674 parser and mixed-reconciliation regressions."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.restore_baseline_isolated import validate_volatile_storage

def main():
    volume=ROOT/'auditoria_local/recovery_19nov_capacity';os.environ['FUNK_RECOVERY_WORK_ROOT']=str(volume)
    if validate_volatile_storage(minimum_bytes=8*1024**3)!=volume:raise ValueError('volume_invalid')
    suites=('tests.test_docent_bag_404_postgres','tests.test_difficult_joint_postgres','tests.test_mixed_reconciliation_postgres')
    command=[str(ROOT/'.venv/bin/python'),'-B','-m','unittest',*suites]
    result=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=600)
    evidence={'suites':suites,'exit_code':result.returncode,'output_sha256':hashlib.sha256(result.stdout).hexdigest(),
              'tests_sha256':{name:hashlib.sha256((ROOT/Path(name.replace('.','/')+'.py')).read_bytes()).hexdigest() for name in suites}}
    path=volume/('regression_404_674_'+uuid.uuid4().hex+'.json')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(evidence,f);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps({'status':'pass' if result.returncode==0 else 'failed','receipt':str(path)}));return result.returncode

if __name__=='__main__':sys.exit(main())

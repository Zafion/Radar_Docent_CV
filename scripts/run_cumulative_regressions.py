"""Run isolated synthetic PostgreSQL regression and write a private receipt."""
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
    root=ROOT/'auditoria_local/recovery_19nov_capacity';os.environ['FUNK_RECOVERY_WORK_ROOT']=str(root)
    if validate_volatile_storage(minimum_bytes=512*1024**2)!=root:raise ValueError('work_root_invalid')
    suite='tests.test_historical_cumulative_real_adapters'
    command=[str(ROOT/'.venv/bin/python'),'-B','-m','unittest',suite]
    p=subprocess.run(command,cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=300)
    report={'suite':suite,'exit_code':p.returncode,'output_sha256':hashlib.sha256(p.stdout).hexdigest(),
            'test_sha256':hashlib.sha256((ROOT/'tests/test_historical_cumulative_real_adapters.py').read_bytes()).hexdigest()}
    path=root/('synthetic_regression_'+uuid.uuid4().hex+'.json')
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f);f.write('\n');f.flush();os.fsync(f.fileno())
    print(json.dumps({'status':'pass' if p.returncode==0 else 'failed','receipt':str(path)}))
    return p.returncode

if __name__=='__main__':sys.exit(main())

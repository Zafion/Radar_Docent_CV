"""Interactive, unprivileged coordinator for one authenticated encrypted copy.

Only fixed system executables are invoked with sudo. The AES key stays in this
process while the isolated restoration consumes the copy.
"""
import ctypes
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.services.encrypted_baseline_copy import encrypt_copy,decrypt_verified
from scripts.restore_baseline_isolated import restore_ciphertext,validate_volatile_storage

LAB=Path('/srv/funkcionario-recovery-lab')
DUMP=LAB/'incoming/backup31oct.dump'
COMMIT=LAB/'private/auth-0c397ee81047c4fd9ccfd8e1/commit.json'
ACK=LAB/'incoming/coordinator_result.json'
BASE=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
SIZE=67417883
RUN='0c397ee81047c4fd9ccfd8e1'


def need(condition,code):
    if not condition:raise ValueError(code)


def command(args,limit=131072):
    process=subprocess.run(args,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,timeout=300,check=False)
    need(process.returncode==0 and len(process.stdout)<=limit,'system_command_failed_'+Path(args[0]).name)
    return process.stdout


def privileged(*args,limit=131072):
    return command(['/usr/bin/sudo','-n','--',*args],limit=limit)


def snapshot():
    # %F is translated according to the operator's locale. The hexadecimal
    # st_mode from %f is locale-independent and also encodes the file type.
    raw=privileged('/usr/bin/stat','--printf=%d %i %s %u %g %a %h %Y %Z %f',str(DUMP))
    fields=raw.decode('ascii').split()
    need(len(fields)==10,'dump_metadata_invalid')
    try: mode=int(fields[9],16)
    except ValueError:raise ValueError('dump_metadata_invalid') from None
    need(stat.S_ISREG(mode),'dump_not_regular')
    try: values=tuple(map(int,fields[:9]))
    except ValueError:raise ValueError('dump_metadata_invalid') from None
    need(values[:7]==(LAB.stat().st_dev,261649,SIZE,995,995,600,1), 'dump_metadata_mismatch')
    need(stat.S_IMODE(mode)==0o600,'dump_metadata_mismatch')
    return fields


def digest():
    line=privileged('/usr/bin/sha256sum','--',str(DUMP)).decode('ascii')
    token=line.split(' ',1)[0]
    need(re.fullmatch(r'[0-9a-f]{64}',token) is not None,'invalid_digest')
    return token


def main():
    need(os.getuid()==1000 and os.geteuid()==1000 and sys.stdin.isatty() and sys.stdout.isatty(),'ordinary_user_tty_required')
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    libc=ctypes.CDLL(None,use_errno=True)
    need(libc.prctl(4,0,0,0,0)==0,'disable_process_dump_failed')
    def interrupted(signum,frame):raise InterruptedError('interrupted')
    signal.signal(signal.SIGHUP,interrupted);signal.signal(signal.SIGTERM,interrupted)
    mount=command(['/usr/bin/findmnt','--noheadings','--raw','--mountpoint',str(LAB),'--output','SOURCE,FSTYPE,OPTIONS']).decode().strip().split()
    need(len(mount)==3 and mount[0]=='/dev/mapper/funkrec_lab_20261107' and mount[1]=='ext4'
         and {'ro','nodev','nosuid','noexec'}<=set(mount[2].split(','))
         and bool({'noload','norecovery'} & set(mount[2].split(','))),'source_mount_not_readonly')
    need(os.statvfs(BASE).f_bavail*os.statvfs(BASE).f_frsize>=SIZE*3,'copy_space_insufficient')
    validate_volatile_storage()
    print('Se solicitará sudo en esta terminal; la clave no se registra.',flush=True)
    auth=subprocess.run(['/usr/bin/sudo','-v'],check=False)
    need(auth.returncode==0,'sudo_authentication_failed')
    before=snapshot()
    commit=json.loads(privileged('/usr/bin/cat','--',str(COMMIT)))
    expected=commit.get('sha256')
    need(isinstance(expected,str) and re.fullmatch(r'[0-9a-f]{64}',expected),'commit_digest_invalid')
    need(commit=={'action':'COMMIT','run_id':RUN,'sha256':expected,'source_exit_code':0,'prepared_validated':True},'commit_receipt_invalid')
    ack=json.loads(privileged('/usr/bin/cat','--',str(ACK)))
    need(ack.get('status')=='COORDINATOR_DONE' and ack.get('ack')=={'status':'PUBLISHED','size':SIZE,'sha256':expected},'ack_receipt_invalid')
    need(digest()==expected,'source_sha_before_copy_mismatch')
    session=BASE/('baseline_copy_'+os.urandom(12).hex())
    session.mkdir(mode=0o700)
    need(session.stat().st_uid==1000 and stat.S_IMODE(session.stat().st_mode)==0o700,'copy_session_permissions')
    output=session/'baseline.aesgcm'
    source=subprocess.Popen(['/usr/bin/sudo','-n','--','/usr/bin/cat','--',str(DUMP)],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    try:
        receipt=encrypt_copy(source.stdout,output,expected,SIZE)
        source.stdout.close();need(source.wait(timeout=60)==0,'source_copy_exit')
    finally:
        if source.poll() is None:source.terminate();source.wait(timeout=10)
    need(snapshot()==before and digest()==expected,'source_changed_after_copy')
    plaintext=decrypt_verified(output,receipt['key'],expected,SIZE)
    need(hashlib.sha256(plaintext).hexdigest()==expected,'copy_verification_failed')
    del plaintext
    print('Copia cifrada verificada; comienza la restauración aislada.',flush=True)
    work,result=restore_ciphertext(output,receipt['key'],expected,SIZE)
    public={'status':'BASELINE_RESTORED','session':str(session),'volatile_cluster':str(work),'source_sha256':expected,
            'source_size':SIZE,'ciphertext_sha256':receipt['ciphertext_sha256'],'key_on_disk':False,
            'source_unchanged':True,'credentials_exported':False,'restoration':result,
            'persistence':'encrypted_volume' if os.environ.get('FUNK_RECOVERY_WORK_ROOT','').strip() else 'volatile_tmpfs_until_reboot','postgres_stopped':True}
    meta=session/'receipt.json';meta.write_text(json.dumps(public,indent=2)+'\n');meta.chmod(0o600)
    print(json.dumps(public),flush=True)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        print(json.dumps({'status':'STOP','condition':str(exc) if isinstance(exc,ValueError) else type(exc).__name__,
                          'automatic_retry':False,'credentials_exported':False}),flush=True)
        sys.exit(1)

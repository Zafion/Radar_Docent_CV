"""Restore the verified 17 November archive without touching the original."""
import json,os,stat,sys,uuid
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.services.encrypted_baseline_copy import encrypt_copy
from scripts.restore_baseline_isolated import restore_ciphertext,validate_volatile_storage
SHA='2abcde4ebb9914b41164885048d53c7aca6da3f5c141b3371a306e2048642d35'
SIZE=71408857

def run():
    root=validate_volatile_storage(minimum_bytes=6*1024**3)
    if root==Path('/dev/shm'):raise ValueError('luks_volume_required')
    source=root/'baseline_17nov.dump';receipt=root/'baseline_17nov.receipt.json'
    dest=root/('baseline_17nov.dr.restore.json' if '--dr-test' in sys.argv else 'baseline_17nov.restore.json')
    if os.path.lexists(dest):raise ValueError('restore_receipt_already_exists')
    info=source.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or stat.S_IMODE(info.st_mode)!=0o600 or info.st_nlink!=1 or info.st_size!=SIZE:raise ValueError('source_identity')
    result=json.loads(receipt.read_text())
    if result.get('status')!='VERIFIED' or result.get('sha256')!=SHA or result.get('size')!=SIZE:raise ValueError('receipt_mismatch')
    temp=root/('.restore_cipher_'+uuid.uuid4().hex)
    created=False
    try:
        fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
        with os.fdopen(fd,'rb') as stream:
            encrypted=encrypt_copy(stream,temp,SHA,SIZE);created=True
        work,restoration=restore_ciphertext(temp,encrypted['key'],SHA,SIZE)
    finally:
        if created:temp.unlink()
    public={'status':'BASELINE_RESTORED','source_sha256':SHA,'source_size':SIZE,'work_directory':str(work),
            'persistence':'encrypted_luks_volume','postgres_stopped':True,'restoration':restoration}
    fd=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as stream:json.dump(public,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
    print(json.dumps(public))

if __name__=='__main__':
    try:run()
    except Exception as exc:
        print(json.dumps({'status':'STOP','condition':str(exc) if isinstance(exc,(ValueError,RuntimeError)) else type(exc).__name__,'automatic_retry':False}))
        sys.exit(1)

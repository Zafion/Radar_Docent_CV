"""Restore the verified local 21 November archive into a new isolated LUKS cluster."""
import json
import os
from pathlib import Path
import stat
import sys
import uuid
import argparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.encrypted_baseline_copy import encrypt_copy
from scripts.restore_baseline_isolated import restore_ciphertext, validate_volatile_storage

SOURCE = ROOT / 'auditoria_local/recuperacion_17nov_luks/baseline_21nov_20261124/baseline.dump'
WORK = ROOT / 'auditoria_local/recovery_final_capacity'
SHA = '4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b'
SIZE = 71320284


def run(work_root=WORK, source=SOURCE, receipt_dir=None):
    work_root = Path(work_root)
    source = Path(source)
    receipt_dir = Path(receipt_dir) if receipt_dir is not None else work_root
    os.environ['FUNK_RECOVERY_WORK_ROOT'] = str(work_root)
    if validate_volatile_storage(minimum_bytes=20 * 1024**3) != work_root:
        raise ValueError('wrong_work_volume')
    if receipt_dir.is_symlink() or not receipt_dir.is_dir() or receipt_dir.resolve() != receipt_dir or not receipt_dir.is_relative_to(work_root):
        raise ValueError('receipt_directory_invalid')
    rst = receipt_dir.stat()
    if rst.st_uid != os.getuid() or stat.S_IMODE(rst.st_mode) != 0o700:
        raise ValueError('receipt_directory_permissions')
    st = source.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size != SIZE:
        raise ValueError('source_identity')
    receipt = json.loads((source.parent / 'receipt.json').read_text())
    if receipt.get('status') != 'VERIFIED' or receipt.get('local_sha256') != SHA or receipt.get('size') != SIZE:
        raise ValueError('source_receipt_mismatch')
    dest = receipt_dir / 'baseline_21nov.restore.json'
    if os.path.lexists(dest):
        raise ValueError('restore_receipt_already_exists')
    encrypted_path = work_root / ('.restore_cipher_' + uuid.uuid4().hex)
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as stream:
            encrypted = encrypt_copy(stream, encrypted_path, SHA, SIZE)
        work, restoration = restore_ciphertext(encrypted_path, encrypted['key'], SHA, SIZE)
    finally:
        encrypted_path.unlink(missing_ok=True)
    result = {'status': 'BASELINE_RESTORED', 'source_sha256': SHA, 'source_size': SIZE,
              'work_directory': str(work), 'persistence': 'encrypted_luks_volume',
              'postgres_stopped': True, 'restoration': restoration}
    fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(result))


if __name__ == '__main__':
    try:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument('--work-root', type=Path, default=WORK)
        parser.add_argument('--dump', type=Path, default=SOURCE)
        parser.add_argument('--receipt-dir', type=Path)
        args = parser.parse_args()
        run(args.work_root, args.dump, args.receipt_dir)
    except Exception as exc:
        print(json.dumps({'status': 'STOP', 'condition': str(exc) if isinstance(exc, (ValueError, RuntimeError)) else type(exc).__name__, 'automatic_retry': False}))
        sys.exit(1)

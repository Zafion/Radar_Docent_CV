"""Restore a verified snapshot to a new isolated encrypted local PG16 cluster."""
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.services.encrypted_baseline_copy import encrypt_copy
from scripts.restore_baseline_isolated import restore_ciphertext, validate_volatile_storage


def main():
    if len(sys.argv) != 2:
        raise SystemExit('usage: restore_productive_snapshot_local.py SNAPSHOT_DIRECTORY')
    directory = Path(sys.argv[1])
    volume = ROOT / 'auditoria_local/recovery_19nov_capacity'
    os.environ['FUNK_RECOVERY_WORK_ROOT'] = str(volume)
    if validate_volatile_storage(minimum_bytes=20 * 1024**3) != volume:
        raise ValueError('unsafe_volume')
    if directory.is_symlink() or directory.resolve() != directory or directory.parent != volume:
        raise ValueError('unsafe_snapshot_directory')
    if directory.stat().st_uid != os.getuid() or stat.S_IMODE(directory.stat().st_mode) != 0o700:
        raise ValueError('unsafe_snapshot_permissions')
    source = directory / 'snapshot.dump'
    st = source.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600:
        raise ValueError('unsafe_dump')
    receipt = json.loads((directory / 'receipt.json').read_text())
    if receipt.get('status') != 'VERIFIED_TOC' or receipt.get('size_bytes') != st.st_size:
        raise ValueError('invalid_receipt')
    fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    with os.fdopen(fd, 'rb') as source_stream:
        sha = hashlib.file_digest(source_stream, 'sha256').hexdigest()
    if sha != receipt.get('sha256'):
        raise ValueError('sha_mismatch')
    output = directory / 'restore.json'
    if os.path.lexists(output):
        raise ValueError('receipt_exists')
    temp = volume / ('.snapshot_cipher_' + uuid.uuid4().hex)
    try:
        fd = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        with os.fdopen(fd, 'rb') as source_stream:
            encrypted = encrypt_copy(source_stream, temp, sha, st.st_size)
        work, restoration = restore_ciphertext(temp, encrypted['key'], sha, st.st_size)
    finally:
        temp.unlink(missing_ok=True)
    result = {'status': 'BASELINE_RESTORED', 'source_sha256': sha, 'work_directory': str(work), 'restoration': restoration}
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(result, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(result))


if __name__ == '__main__':
    main()

"""Authenticated temporary baseline copies; cryptographic key stays in memory."""
import hashlib
import os
from pathlib import Path
import re
import stat

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC=b'FUNK_BASELINE_AESGCM_V1\0'
AAD=b'funkcionario-isolated-baseline-v1'


def encrypt_copy(source, destination: Path, expected_sha256: str, expected_size: int):
    """Copy to a new 0600 ciphertext file, checking the entire plaintext first.

    Returns an in-memory key; callers must never log or persist it. A process
    interruption loses the key, making the temporary ciphertext unreadable.
    """
    if not re.fullmatch(r'[0-9a-f]{64}',expected_sha256) or type(expected_size) is not int or not 0<expected_size<=1024**3:
        raise ValueError('invalid_approved_identity')
    data=source.read(expected_size+1)
    if len(data)!=expected_size or hashlib.sha256(data).hexdigest()!=expected_sha256:
        raise ValueError('source_size_or_sha_mismatch')
    key=os.urandom(32);nonce=os.urandom(12)
    ciphertext=AESGCM(key).encrypt(nonce,data,AAD)
    path=Path(destination)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
    try:
        with os.fdopen(fd,'wb') as out:
            out.write(MAGIC+nonce+ciphertext);out.flush();os.fsync(out.fileno())
    except BaseException:
        # Preserve any incomplete ciphertext for inspection; never call it valid.
        raise
    return {'key':key,'plaintext_sha256':expected_sha256,'size_bytes':expected_size,
            'ciphertext_sha256':hashlib.sha256(MAGIC+nonce+ciphertext).hexdigest()}


def decrypt_verified(path: Path, key: bytes, expected_sha256: str, expected_size: int) -> bytes:
    """Authenticate all ciphertext before returning any plaintext bytes."""
    fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_CLOEXEC)
    try:
        st=os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.getuid() or stat.S_IMODE(st.st_mode)!=0o600 or st.st_nlink!=1:
            raise ValueError('ciphertext_identity')
        if st.st_size!=len(MAGIC)+12+expected_size+16:
            raise ValueError('ciphertext_size')
        with os.fdopen(fd,'rb',closefd=False) as inp:data=inp.read(st.st_size+1)
        after=os.fstat(fd)
        stable=lambda s:(s.st_dev,s.st_ino,s.st_mode,s.st_uid,s.st_gid,s.st_nlink,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
        if len(data)!=st.st_size or stable(after)!=stable(st) or not data.startswith(MAGIC):
            raise ValueError('ciphertext_changed_or_header_invalid')
        start=len(MAGIC);plain=AESGCM(key).decrypt(data[start:start+12],data[start+12:],AAD)
        if len(plain)!=expected_size or hashlib.sha256(plain).hexdigest()!=expected_sha256:
            raise ValueError('decrypted_identity_mismatch')
        return plain
    finally:os.close(fd)

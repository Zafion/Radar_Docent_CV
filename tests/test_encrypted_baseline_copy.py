import hashlib
import io
import os
from pathlib import Path
import tempfile
import unittest

from app.services.encrypted_baseline_copy import decrypt_verified,encrypt_copy


class EncryptedBaselineCopyTests(unittest.TestCase):
    def test_access_time_change_does_not_reject_unchanged_ciphertext(self):
        plain=b'synthetic baseline';digest=hashlib.sha256(plain).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'copy.enc';receipt=encrypt_copy(io.BytesIO(plain),path,digest,len(plain))
            st=path.stat();os.utime(path,ns=(1,st.st_mtime_ns))
            self.assertEqual(decrypt_verified(path,receipt['key'],digest,len(plain)),plain)
    def test_roundtrip_is_ciphertext_and_rejects_tampering(self):
        plain=b'synthetic baseline only\x00'*2000;digest=hashlib.sha256(plain).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'copy.enc';receipt=encrypt_copy(io.BytesIO(plain),path,digest,len(plain))
            self.assertNotIn(b'synthetic baseline only',path.read_bytes())
            self.assertEqual(decrypt_verified(path,receipt['key'],digest,len(plain)),plain)
            damaged=bytearray(path.read_bytes());damaged[-17]^=1;path.write_bytes(damaged);path.chmod(0o600)
            with self.assertRaises(Exception):decrypt_verified(path,receipt['key'],digest,len(plain))

    def test_wrong_identity_or_existing_file_stops(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'copy.enc';digest=hashlib.sha256(b'expected').hexdigest()
            with self.assertRaisesRegex(ValueError,'source_size_or_sha_mismatch'):
                encrypt_copy(io.BytesIO(b'wrong'),path,digest,len(b'expected'))
            self.assertFalse(path.exists())
            path.write_bytes(b'existing')
            with self.assertRaises(FileExistsError):encrypt_copy(io.BytesIO(b'expected'),path,digest,len(b'expected'))
            self.assertEqual(path.read_bytes(),b'existing')


if __name__=='__main__':unittest.main()

import unittest

from app.services.selective_runner import run_synthetic_v3


class SyntheticScopeTests(unittest.TestCase):
    def test_non_fixture_scope_is_rejected_before_database_access(self):
        with self.assertRaisesRegex(ValueError,'synthetic_scope_required'):
            run_synthetic_v3(root='/srv/funkcionario',cluster_token='a'*32,staging='/srv/funkcionario/staging',
                manifest_bytes=b'{}',manifest_sha256='0'*64,lock_paths=['/tmp/a','/tmp/b'])


if __name__=='__main__':unittest.main()

"""Synthetic-only supervised worker for a verified temporary cluster."""
from contextlib import ExitStack
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

from tests.postgres_isolated_validation import FreshCluster, connection_guards
from tests.selective_synthetic_runner import run_guarded_fixture
from tests.test_non_docent_extraction_safety import AWARD


def main():
    root=Path(sys.argv[1]).resolve()
    cluster=FreshCluster.__new__(FreshCluster)
    cluster.root=root; cluster.data=root/'d'; cluster.sock=root/'s'; cluster.token=sys.argv[2]
    paths=[root/'lock-one',root/'lock-two']
    raw=(root/'manifest.json').read_bytes()
    import hashlib
    digest=hashlib.sha256(raw).hexdigest()
    os.environ['RADAR_DOCENT_DB_URL']=cluster.dsn()
    os.environ['ENABLE_NOTIFICATIONS']='0'
    with ExitStack() as stack:
        list(connection_guards(cluster,stack))
        from app.storage.db import close_pool
        stack.callback(close_pool)
        from app.services import non_docent_parser as parser
        from app.services.selective_lock_supervisor import InheritedLockFence
        from app.storage import db
        stack.enter_context(patch.object(parser.NonDocentParserService,'_extract_pages_text',return_value=[AWARD]))
        stack.enter_context(patch('app.services.assignment_matcher.AssignmentMatcherService.match_all',side_effect=AssertionError('matcher')))
        stack.enter_context(patch('app.services.notification_eligibility.reconcile_if_configured',side_effect=AssertionError('reconcile')))
        counts={'checks':0,'commits':0}
        original_check=InheritedLockFence.check
        original_commit=db.PgCompatConnection.commit
        def observed_check(fence):
            original_check(fence)
            counts['checks']+=1
        def observed_commit(conn):
            counts['commits']+=1
            if sys.argv[3]=='fault' and counts['commits']==1:
                # Fault injection is limited to the temporary-cluster fixture.
                fence=db._write_fence.get()
                os.close(fence.fds[0])
            return original_commit(conn)
        stack.enter_context(patch.object(InheritedLockFence,'check',observed_check))
        stack.enter_context(patch.object(db.PgCompatConnection,'commit',observed_commit))
        try:
            result=run_guarded_fixture(cluster,root/'staging',raw,digest,paths)
            status='completed'
        except (OSError,RuntimeError,ValueError):
            result=None; status='stopped'
        (root/'worker-result.json').write_text(json.dumps(dict(status=status,result=result,**counts)))
    return 0 if status=='completed' else 2


if __name__=='__main__':
    raise SystemExit(main())

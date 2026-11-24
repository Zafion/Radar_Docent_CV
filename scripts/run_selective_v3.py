#!/usr/bin/env python3
"""Selective v3 entrypoint; only isolated synthetic test authority exists."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.selective_runner import run_synthetic_v3
from app.services.selective_db_fence import TransactionAuthorization


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--synthetic-test-root',required=True)
    parser.add_argument('--synthetic-token',required=True)
    parser.add_argument('--synthetic-fence-token',required=True)
    parser.add_argument('--synthetic-fence-generation',required=True,type=int)
    parser.add_argument('--synthetic-baseline-id',required=True)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--staging',required=True)
    parser.add_argument('--lock-one',required=True)
    parser.add_argument('--lock-two',required=True)
    args=parser.parse_args()
    try:
        with open(args.manifest,'rb') as f: raw=f.read(1024*1024+1)
        result=run_synthetic_v3(root=args.synthetic_test_root,cluster_token=args.synthetic_token,
            staging=args.staging,manifest_bytes=raw,manifest_sha256=args.manifest_sha256,
            lock_paths=[args.lock_one,args.lock_two],baseline_id=args.synthetic_baseline_id,authorization=TransactionAuthorization(
                'synthetic-cycle',args.synthetic_fence_generation,args.synthetic_fence_token))
        status='COMPLETED_WITH_PARSE_FAILURES_SYNTHETIC_ONLY' if result['failed'] else 'COMPLETED_SYNTHETIC_ONLY'
        print(json.dumps({'status':status,'result':result}))
        return 0 if not result['failed'] else 2
    except Exception as exc:
        print(json.dumps({'status':'STOP','category':type(exc).__name__}))
        return 2


if __name__=='__main__':
    raise SystemExit(main())

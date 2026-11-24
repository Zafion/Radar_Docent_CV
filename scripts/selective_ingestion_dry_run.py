#!/usr/bin/env python3
"""Read-only screening only; does not authorize or execute ingestion."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.services.selective_dry_run import dry_run
from app.services.selective_lock_supervisor import InheritedLockFence


def bounded_read(path, limit):
    with open(path,'rb') as handle:
        value=handle.read(limit+1)
    if len(value)>limit:
        raise ValueError('input_too_large')
    return value


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--staging',required=True)
    parser.add_argument('--manifest',required=True)
    parser.add_argument('--manifest-sha256',required=True)
    parser.add_argument('--inventory',required=True)
    parser.add_argument('--inventory-sha256',required=True)
    args=parser.parse_args()
    try:
        fence=InheritedLockFence(['/tmp/radar_docent_cv_pipeline.lock','/tmp/funkcionario-remote-fetch-import.lock'])
        result=dry_run(staging=args.staging,manifest=bounded_read(args.manifest,1024*1024),
                       manifest_sha256=args.manifest_sha256,inventory_bytes=bounded_read(args.inventory,16*1024*1024),
                       inventory_sha256=args.inventory_sha256,fence=fence)
        print(json.dumps(result)); return 2
    except Exception as exc:
        print(json.dumps({'status':'STOP','category':type(exc).__name__,'writes_performed':False})); return 2


if __name__=='__main__':
    raise SystemExit(main())

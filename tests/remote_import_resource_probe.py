"""Offline synthetic DR04 resource probe, no database, one fresh process per case."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import sys
import tempfile
import time
import tracemalloc

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.services.remote_import_integrity import validated_manifest, publish_snapshot, ImportIntegrityError


def main(output,count,size,fd_limit=0):
    output=output.resolve()
    if not output.is_relative_to(ROOT/'auditoria_local/resultados') or output.exists():raise ValueError('new ignored output required')
    output.mkdir(parents=True)
    free=os.statvfs(output).f_bavail*os.statvfs(output).f_frsize
    if free<count*size*2+100*1024**2:raise RuntimeError('insufficient_probe_disk')
    before_fds=len(os.listdir('/proc/self/fd'));started=time.monotonic()
    report={'count':count,'bytes_per_result':size,'snapshot_bytes_expected':count*size,'fd_limit_requested':fd_limit,'database_connections':0,'external_calls':0}
    with tempfile.TemporaryDirectory(prefix='resource_',dir=output) as tmp:
        root=Path(tmp);incoming=root/'incoming';incoming.mkdir()
        digest=hashlib.sha256();remaining=size
        with (incoming/'synthetic.bin').open('wb') as handle:
            while remaining:
                chunk=b'x'*min(1024*1024,remaining);handle.write(chunk);digest.update(chunk);remaining-=len(chunk)
        sha=digest.hexdigest();del chunk
        rows=[]
        for i in range(count):
            rows.append(dict(status='downloaded',sha256=sha,size_bytes=size,stored_filename='synthetic.bin',original_filename='synthetic.bin',relative_file_path='synthetic.bin',asset=dict(source_key='synthetic',source_url='https://example.invalid',canonical_url=f'https://example.invalid/{i}',url=f'https://example.invalid/{i}',asset_role='pdf_candidate',title='Synthetic',downloadable=True)))
        manifest=incoming/'manifest.json';manifest.write_text(json.dumps({'schema_version':1,'sources':[dict(source_key='synthetic',source_url='https://example.invalid',results=rows)]}));del rows
        if fd_limit:
            soft,hard=resource.getrlimit(resource.RLIMIT_NOFILE);resource.setrlimit(resource.RLIMIT_NOFILE,(min(fd_limit,hard),hard))
        tracemalloc.start()
        baseline_fs=os.statvfs(output).f_bavail*os.statvfs(output).f_frsize
        try:
            with validated_manifest(manifest,incoming,root/'data') as (parsed,snapshots):
                report['peak_open_fds_observed']=len(os.listdir('/proc/self/fd'))
                report['peak_fd_delta_observed']=report['peak_open_fds_observed']-before_fds
                report['allocated_snapshot_bytes_pinned']=sum(os.fstat(h.fileno()).st_blocks*512 for h in snapshots.values())
                report['filesystem_free_delta_shared_observed']=baseline_fs-os.statvfs(output).f_bavail*os.statvfs(output).f_frsize
                result=parsed['sources'][0]['results'][0]
                dest=publish_snapshot(snapshots[id(result)],data_dir=root/'data',source_key='synthetic',filename='synthetic.bin',sha256=sha,size_bytes=size)
                digest=hashlib.sha256()
                with dest.open('rb') as handle:
                    for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
                report['published_bytes_valid']=dest.stat().st_size==size and digest.hexdigest()==sha
                report['final_mode']=oct(dest.stat().st_mode&0o777)
                report['status']='passed'
        except ImportIntegrityError as exc:
            report.update(status='rejected_by_resource_limit' if fd_limit else 'failed',reason=exc.reason)
    report.update(elapsed_seconds=round(time.monotonic()-started,4),python_peak_bytes=tracemalloc.get_traced_memory()[1],rss_high_water_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,final_open_fds=len(os.listdir('/proc/self/fd')),initial_open_fds=before_fds,temporary_tree_removed=not root.exists())
    tracemalloc.stop();(output/'results.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(report),flush=True)
    return 0 if (report['status']=='passed' or fd_limit and report['status']=='rejected_by_resource_limit') and report['final_open_fds']==before_fds else 1


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output-dir',required=True,type=Path);p.add_argument('--count',type=int,required=True);p.add_argument('--size',type=int,default=454352);p.add_argument('--fd-limit',type=int,default=0)
    a=p.parse_args();raise SystemExit(main(a.output_dir,a.count,a.size,a.fd_limit))

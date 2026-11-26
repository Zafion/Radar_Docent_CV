"""Run the recovery coordinator against explicitly supplied isolated databases."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import psycopg
from psycopg.rows import dict_row
from app.services.historical_cumulative_reconcile import reconcile_cumulative
from scripts.restore_baseline_isolated import validate_volatile_storage

PLAN=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json'


def manifest():
    entries={x['version_id']:x for x in json.loads(PLAN.read_text())['entries']}
    versions=(700,436,701,702,1000391,1000067,1000444,1000005,1000535)
    if any(v not in entries for v in versions):raise ValueError('manifest_document_missing')
    def item(v):
        entry=entries[v];path=Path(entry['local_sha_verified_paths'][0])
        original_root=Path('/home/zafion/Proyects/Radar_Docent_CV')
        if path.is_absolute() and path.is_relative_to(original_root):path=ROOT/path.relative_to(original_root)
        spec={'document_sha':entry['sha256'],'pdf_path':path}
        evidence=entry['provenance']['evidence']
        if evidence:
            capture=evidence[0][0];html=(ROOT/capture['path']).read_bytes()
            spec['provenance']={'html_bytes':html,'html_sha256':capture['sha256'],
                'page_url':capture['final_url'],'document_url':entry['provenance']['urls'][0]}
        return spec
    docs={str(v):item(v) for v in (700,436,701,1000067,1000444)}
    docs['expert']=item(1000391);docs['expert']['procedure_key']='expertos-2026-11-05'
    docs['700']['accredited_offer_sha']=entries[702]['sha256']
    docs['700']['accredited_offer_pdf_path']=item(702)['pdf_path']
    offers=[item(1000005),item(1000535)]
    for spec,label in zip(offers,('provisional','definitivo')):spec['stage_label']=label
    return docs,offers


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--socket',required=True,type=Path);p.add_argument('--port',required=True,type=int)
    p.add_argument('--source-db',required=True);p.add_argument('--destination-db',required=True)
    p.add_argument('--user',required=True);p.add_argument('--work-root',required=True,type=Path)
    p.add_argument('--receipt-dir',required=True,type=Path);p.add_argument('--mode',required=True,choices=('plan','apply'))
    a=p.parse_args()
    if not a.socket.is_absolute() or a.socket.is_symlink() or not a.socket.is_dir():raise ValueError('private_socket_directory_required')
    if a.source_db==a.destination_db or a.source_db!='reconstruction_dr' or a.destination_db!='baseline_future_trial':raise ValueError('database_identity_not_authorized')
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(a.work_root)
    if validate_volatile_storage(minimum_bytes=6*1024**3)!=a.work_root:raise ValueError('work_volume_invalid')
    receipt_dir=a.receipt_dir
    if receipt_dir.is_symlink() or not receipt_dir.is_dir() or receipt_dir.resolve()!=receipt_dir or not receipt_dir.is_relative_to(a.work_root):raise ValueError('receipt_directory_invalid')
    if receipt_dir.stat().st_uid!=os.getuid() or receipt_dir.stat().st_mode & 0o077:raise ValueError('receipt_permissions_invalid')
    docs,offers=manifest();ident=uuid.uuid4().hex
    target=receipt_dir/('cumulative_'+ident+'.json');temporary=receipt_dir/('.cumulative_'+ident+'.tmp')
    def save(report):
        payload={'schema':'cumulative_trial_v1','source_db':a.source_db,'destination_db':a.destination_db,
                 'manifest_sha256':hashlib.sha256(PLAN.read_bytes()).hexdigest(),
                 'expected_baseline_sha256':'4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b',
                 'expected_reconstruction_sha256':'b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a','report':report}
        fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:json.dump(payload,f,ensure_ascii=False,default=str);f.write('\n');f.flush();os.fsync(f.fileno())
        os.replace(temporary,target)
    kw={'host':str(a.socket),'port':a.port,'user':a.user,'row_factory':dict_row,'connect_timeout':5}
    state={'report':{'status':'in_progress','documents':{}}}
    def progress(report):
        state['report']=report.copy()
        save(report)
    progress(state['report'])
    try:
        with psycopg.connect(**kw,dbname=a.source_db,options='-c default_transaction_read_only=on') as source, \
             psycopg.connect(**kw,dbname=a.destination_db) as destination:
            result=reconcile_cumulative(source,destination,documents=docs,offers=offers,execute=a.mode=='apply',progress=progress)
        save(result)
    except Exception as exc:
        reason=str(exc)
        safe_reason=reason if isinstance(exc,ValueError) and reason and len(reason)<80 and all(c.isascii() and (c.isalnum() or c=='_') for c in reason) else None
        failed=dict(state['report']);failed.update(status='failed',error_type=type(exc).__name__,reason=safe_reason);save(failed)
        raise
    print(json.dumps({'status':result['status'],'receipt':str(target)}))
    return 0 if result['status']=='planned' else 2


if __name__=='__main__':
    try:sys.exit(main())
    except Exception as exc:
        print(json.dumps({'status':'failed','error_type':type(exc).__name__}),file=sys.stderr);sys.exit(1)

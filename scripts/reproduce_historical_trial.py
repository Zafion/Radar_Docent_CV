"""Single interactive local reproduction, executed only as the ordinary user."""
import hashlib,json,os,subprocess,sys,tempfile
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120'
PYTHON=ROOT/'.venv/bin/python'
SHA='980862fae66d76344a5649b348524c57e0c1660de5784bd58fe1e93f5264852e'
sys.path.insert(0,str(ROOT))

def session_receipts():return {p for p in OUT.glob('baseline_copy_*/receipt.json') if p.is_file() and not p.is_symlink()}

def _hash_file(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def save_receipt(path, data):
    fd, name = tempfile.mkstemp(prefix='.reproduction-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data,stream,ensure_ascii=False,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno())
        os.replace(name,path)
        directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(directory)
        finally:os.close(directory)
    finally:
        if os.path.exists(name):os.unlink(name)

def preflight():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    from scripts.reconcile_batch_trial import verify_evidence
    root=validate_volatile_storage(minimum_bytes=6*1024**3)
    if not os.access(root,os.W_OK):raise ValueError('temporary_storage_not_writable')
    for path in (Path('/usr/bin/bwrap'),Path('/usr/bin/sudo'),Path('/usr/lib/postgresql/16/bin/initdb'),
                 Path('/usr/lib/postgresql/16/bin/pg_restore'),PYTHON):
        if not path.is_file() or not os.access(path,os.X_OK):raise ValueError('required_executable_missing')
    required=(ROOT/'auditoria_local/pgv_hist_20261120/owner.json',OUT/'plan_integral_reanudable.json',
              ROOT/'app/storage/migrations/20261122_document_validation_metadata.sql',
              ROOT/'app/storage/migrations/20261122_historical_procedures.sql')
    if not all(p.is_file() and not p.is_symlink() for p in required):raise ValueError('required_local_evidence_missing')
    for name in ('lote_reconciliacion_01.json','lote_reconciliacion_02.json'):
        data=json.loads((OUT/name).read_text())
        if data.get('format')!='historical-reconciliation-batch-v1':raise ValueError('batch_manifest_invalid')
        verify_evidence(data['documents'])

def run():
    if os.geteuid()==0 or not sys.stdin.isatty() or not sys.stdout.isatty():raise ValueError('ordinary_user_tty_required')
    preflight()
    before=session_receipts()
    subprocess.run([str(PYTHON),'-B',str(ROOT/'scripts/copy_encrypted_baseline_manual.py')],cwd=ROOT,check=True)
    created=session_receipts()-before;eligible=[]
    for path in created:
        row=json.loads(path.read_text())
        if row.get('status')=='BASELINE_RESTORED' and row.get('source_sha256')==SHA:eligible.append(path)
    if len(eligible)!=1:raise ValueError('restored_receipt_ambiguous')
    session=eligible[0].parent
    final=session/'reproduction_receipt.json'
    files=[ROOT/'scripts/reproduce_historical_trial.py',ROOT/'scripts/compare_restored_baseline.py',
           ROOT/'scripts/reconcile_batch_trial.py',ROOT/'scripts/integrate_03nov_trial.py',
           ROOT/'scripts/integrate_0519_trial.py',OUT/'lote_reconciliacion_01.json',OUT/'lote_reconciliacion_02.json',
           ROOT/'app/storage/migrations/20261122_document_validation_metadata.sql',ROOT/'app/storage/migrations/20261122_historical_procedures.sql']
    receipt={'status':'IN_PROGRESS','started_at_utc':datetime.now(timezone.utc).isoformat(),
             'session':session.name,'source_sha256':SHA,'restoration_receipt':str(eligible[0]),
             'artifacts_sha256':{str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p):_hash_file(p) for p in files},
             'phases':{'restore':'PASS'},'final_counts':None,'idempotence':'not_verified',
             'rollback':'not_verified','notifications':'not_verified','evidence':[]}
    save_receipt(final,receipt)
    env=os.environ.copy();env['FUNK_BASELINE_RECEIPT']=str(eligible[0])
    try:
        for flag in ('--trial','--functional-03','--functional-0519','--batch-01','--batch-02','--duplicate-guard'):
            receipt['phases'][flag]='RUNNING';save_receipt(final,receipt)
            subprocess.run([str(PYTHON),'-B',str(ROOT/'scripts/compare_restored_baseline.py'),flag],cwd=ROOT,env=env,check=True)
            receipt['phases'][flag]='PASS';save_receipt(final,receipt)
        # These aggregates are produced by the final batch of this execution, not by a prior receipt.
        evidence=OUT/'reconciliacion_lote_02_20261123.json'
        report=json.loads(evidence.read_text())
        report=report.get('latest_verification',report)
        receipt['final_counts']=report.get('after')
        repeat=report.get('second_execution')
        receipt['idempotence']='verified' if isinstance(repeat,dict) and repeat.get('before') is not None and repeat.get('before')==repeat.get('after') else 'not_verified'
        receipt['rollback']='verified' if report.get('rollback_tested') is True else 'not_verified'
        receipt['notifications']=report.get('external_notifications','not_verified')
        receipt['evidence']=[{'path':str(evidence),'sha256':_hash_file(evidence)}]
        if not receipt['final_counts'] or receipt['idempotence']!='verified' or receipt['rollback']!='verified' or receipt['notifications']!='unchanged':
            raise ValueError('final_batch_evidence_incomplete')
        receipt['status']='REPRODUCED';receipt['finished_at_utc']=datetime.now(timezone.utc).isoformat();save_receipt(final,receipt)
    except BaseException:
        receipt['status']='STOP';receipt['finished_at_utc']=datetime.now(timezone.utc).isoformat();save_receipt(final,receipt)
        raise
    print(json.dumps({'status':'REPRODUCED','receipt':str(final),'volatile_until_reboot':True}),flush=True)

if __name__=='__main__':
    try:run()
    except (ValueError,subprocess.CalledProcessError) as e:
        print(json.dumps({'status':'STOP','condition':str(e) if isinstance(e,ValueError) else 'step_failed','automatic_retry':False}),flush=True)
        sys.exit(1)

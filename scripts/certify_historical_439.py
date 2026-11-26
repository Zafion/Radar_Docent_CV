"""Produce a limited 439 receipt from independent encrypted trial evidence."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
FILES={'trial':'reconciliation_439_trial.json','preservation':'reconciliation_439_preservation.json',
       'evidence':'reconciliation_439_document_evidence.json',
       'previous':'reconciliation_439_previous_fields_560110ddd715463788824c0f0ef4be71.json',
       'source_certificate':'cumulative_certification_final_20261126.json'}


def evaluate(data):
    missing=[k for k in FILES if k not in data]
    if missing:return {'status':'partial','pending':['missing_'+k for k in missing]}
    t=data['trial'];p=data['preservation'];e=data['evidence'];old=data['previous'];cert=data['source_certificate'];pending=[]
    def check(ok,name):
        if not ok:pending.append(name)
    first=t.get('first',{});second=t.get('second',{});before=t.get('before',{});after=t.get('after',{})
    check(cert.get('status')=='complete','source_certification')
    check(t.get('sha256')=='2857d03b84439cd2db19cece2139b097a6dc5a16f34f979044383323eb5f6ab4','document_sha')
    check(first.get('source_rows')==first.get('source_assignments') and first.get('inserted',0)+first.get('quarantined',0)==first.get('source_rows') and first.get('inserted')==first.get('assignments_inserted'),'source_rows_accounted')
    check(second.get('inserted')==0 and second.get('assignments_inserted')==0 and second.get('reused')==first.get('inserted') and second.get('quarantined')==first.get('quarantined'),'idempotence')
    check(after.get('award_results',0)-before.get('award_results',0)==first.get('inserted') and after.get('award_assignments',0)-before.get('award_assignments',0)==first.get('assignments_inserted') and after.get('documents')==before.get('documents'),'global_deltas')
    check(t.get('rollback')=='PASS' and t.get('unvalidated_constraints')==0,'rollback_integrity')
    check(p.get('status')=='PRESERVATION_PASS' and p.get('notification_contents_preserved') is True and p.get('tables',0)>=31,'certified_rows_preserved')
    check(p.get('quarantines',{}).get('recovery_quarantine.positions_19nov')=={'count':598,'content_equal':True} and p.get('quarantines',{}).get('recovery_unawarded.quarantine')=={'count':2548,'content_equal':True},'prior_quarantines')
    check(e.get('status')=='DOCUMENT_EVIDENCE_REGISTERED' and e.get('first',{}).get('quarantined')==first.get('quarantined') and not e.get('second',{}).get('inserted'),'document_evidence')
    version=old.get('version_439',{});check(version.get('identical_record_and_assignment_multiset')==first.get('inserted') and version.get('current_only')==0 and version.get('prior_only')==0,'previous_439_identity')
    check(old.get('documents',{}).get('common')==after.get('documents') and old.get('documents',{}).get('current_only')==0 and old.get('documents',{}).get('prior_only')==0,'document_sha_comparison')
    fields=old.get('field_multisets',{})
    for kind in ('documents','results','assignments','offers','nov19_quarantine_core'):
        check(kind in fields and fields[kind].get('current_only')==0 and fields[kind].get('prior_only')==0,'prior_fields_'+kind)
    check(old.get('procedure_relations',{}).get('current_only')==0 and old.get('procedure_relations',{}).get('prior_only')==0,'procedure_relations')
    differences={k:v for k,v in fields.items() if v.get('current_only') or v.get('prior_only')}
    return {'status':'partial' if pending else 'reconciled_with_prior_differences' if differences else 'complete','pending':pending,
            'document_sha':t.get('sha256'),'first':first,'second':second,'before':before,'after':after,
            'rollback':t.get('rollback'),'prior_candidate_field_differences':differences,
            'prior_candidate_limits':old.get('limits'),'notification_contents_preserved':p.get('notification_contents_preserved'),
            'prior_quarantines':p.get('quarantines')}


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=0)!=VOLUME:raise ValueError('volume_invalid')
    inputs={};hashes={}
    for key,name in FILES.items():
        path=VOLUME/name
        if path.is_symlink() or not path.is_file():raise ValueError('evidence_missing')
        raw=path.read_bytes();inputs[key]=json.loads(raw);hashes[key]=hashlib.sha256(raw).hexdigest()
    result=evaluate(inputs);result.update(created_utc=datetime.now(timezone.utc).isoformat(),evidence_sha256=hashes)
    path=VOLUME/'reconciliation_439_final.json'
    if os.path.lexists(path):raise ValueError('receipt_exists')
    temp=VOLUME/('.439_receipt_'+uuid.uuid4().hex)
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(result,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.link(temp,path,follow_symlinks=False);temp.unlink()
    print(json.dumps({'status':result['status'],'pending':result['pending'],'receipt':str(path)}))
    return 0 if not result['pending'] else 2

if __name__=='__main__':sys.exit(main())

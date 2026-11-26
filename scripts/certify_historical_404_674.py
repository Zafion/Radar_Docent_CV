"""Aggregate local 404/674 evidence without asserting undocumented vigency."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import argparse
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
VOLUME=ROOT/'auditoria_local/recovery_19nov_capacity'
FILES={'previous':'reconciliation_439_final.json','trial':'reconciliation_404_674_trial.json',
       'identity':'reconciliation_404_674_identity.json','preservation':'reconciliation_404_674_preservation.json',
       'quarantine':'reconciliation_404_674_quarantine.json','rollback404':'reconciliation_404_rollback.json',
       'mixed':'reconciliation_404_674_mixed.json',
       'comparison':'reconciliation_404_674_previous_fields_684254caaac546d18cb9a429d8dd86f8.json',
       'metadata':'reconciliation_439_previous_fields_68be014654d843f0aa5e4d4ae7d32ebf.json',
       'prior_notifications':'reconciliation_404_674_previous_fields_562f8cd8a62c4736b8e14fec6e409438.json'}


def evaluate(data):
    required=(*FILES,'regression','pdf_hashes');missing=[x for x in required if x not in data]
    if missing:return {'status':'partial','pending':['missing_'+x for x in missing]}
    trial=data['trial'];identity=data['identity'];preservation=data['preservation'];quarantine=data['quarantine'];comparison=data['comparison'];metadata=data['metadata'];pending=[]
    def check(value,name):
        if not value:pending.append(name)
    check(data['previous'].get('status')=='reconciled_with_prior_differences' and not data['previous'].get('pending'),'prior_candidate_receipt')
    expected={'404':'acf9530d701a06adc5c3c44cc81c3a5554f53dae8e50c13b9f8b3e49a687fcb0','674':'823d06203fa90d2bafefc250793dfafa7141a2b1e1dd3ed2e1ce8a506f9f9793'}
    check(data['pdf_hashes']==expected,'pdf_identity')
    docs=trial.get('documents',{});check(all(docs.get(k,{}).get('status')=='applied' for k in expected),'document_units')
    first404=docs.get('404',{}).get('first',{});first674=docs.get('674',{}).get('first',{})
    check(first404.get('bag_members')==465 and first404.get('ambiguous_rows')==16 and first674.get('difficult_positions')==52 and first674.get('difficult_candidates')==5381,'real_insertions')
    check(identity.get('status')=='DOCUMENT_IDENTITIES_VERIFIED' and identity.get('404',{}).get('reused')==465 and identity.get('404',{}).get('quarantined')==16 and identity.get('674',{}).get('positions_reused')==52 and identity.get('674',{}).get('candidates_reused')==5381 and not identity.get('404',{}).get('inserted') and not identity.get('674',{}).get('candidates_inserted'),'identity_and_replay')
    check(trial.get('rollback_674')=='PASS' and data['rollback404'].get('status')=='ROLLBACK_404_PASS' and trial.get('unvalidated_constraints')==0,'rollback_and_integrity')
    mixed=data['mixed']
    check(mixed.get('status')=='MIXED_REAL_PASS' and all(mixed.get(k,{}).get('actions',{}).get('inserted')==1 for k in ('first404','first674')) and
          all(mixed.get(k,{}).get('actions',{}).get('inserted',0)==0 for k in ('second404','second674')),'mixed_target_reconciliation')
    check(preservation.get('status')=='PRESERVATION_PASS' and preservation.get('notification_contents_preserved') and preservation.get('tables',0)>=34,'certified_rows_and_notifications')
    notification=data['prior_notifications']
    check(notification.get('status')=='NOTIFICATIONS_COMPARED' and all(notification.get('tables',{}).get(k)=={'identical':n,'current_only':0,'prior_only':0} for k,n in (('public_alerts',194),('notification_posts',776),('push_notification_events',204))),'prior_notifications')
    check(quarantine.get('status')=='QUARANTINES_PASS' and quarantine.get('q439')==8 and quarantine.get('q404')==16 and quarantine.get('q19')==598 and quarantine.get('q439_preserved'),'quarantines')
    check(quarantine.get('version674_availability')==[{'availability_status':'unknown','n':52}],'unknown_vigency')
    fields=comparison.get('field_multisets',{})
    for key in ('documents','results','assignments','offers','bag','difficult_positions','difficult_candidates','nov19_quarantine_core'):
        check(key in fields and fields[key].get('current_only')==0 and fields[key].get('prior_only')==0,'previous_fields_'+key)
    check(comparison.get('procedure_relations',{}).get('current_only')==0 and comparison.get('procedure_relations',{}).get('prior_only')==0,'procedure_relations')
    metadata_fields=('availability_checked_at','availability_reason','availability_status','closed_at','closed_by_document_id')
    check(metadata.get('status')=='QUARANTINE_METADATA_PROFILE' and
          all(metadata.get('current',{}).get(k,{}).get('missing')==598 for k in metadata_fields) and
          all(metadata.get('prior',{}).get(k,{}).get('json_null')==598 for k in metadata_fields if k!='availability_status') and
          metadata.get('prior',{}).get('availability_status',{}).get('values')==[{'value':'available','n':598}],'availability_metadata')
    check(data['regression'].get('exit_code')==0,'postgres_regressions')
    check(data['regression'].get('tests_sha256')==data.get('test_hashes') and bool(data.get('test_hashes')),'regression_test_identity')
    return {'status':'partial' if pending else 'reconciled_with_documentary_limits','pending':pending,
        'pdf_hashes':data['pdf_hashes'],'trial':trial,'identity':identity,'quarantines':quarantine,
        'prior_candidate_field_multisets':fields,'procedure_relations':comparison.get('procedure_relations'),
        'metadata_profile':metadata,'baseline_preservation':preservation,'prior_notifications':notification,
        'limitations':['The 598 offers remain quarantined; prior available values match a schema default but their individual origin and current vigency are unproven.',
                       'The 16 ambiguous 404 rows and eight ambiguous 439 rows remain excluded.',
                       'Masked identifiers alone do not establish cross-document identity.',
                       'Field-multiset equality does not distinguish identical repeated records.']}


def main():
    from scripts.restore_baseline_isolated import validate_volatile_storage
    os.environ['FUNK_RECOVERY_WORK_ROOT']=str(VOLUME)
    if validate_volatile_storage(minimum_bytes=0)!=VOLUME:raise ValueError('volume_invalid')
    paths={k:VOLUME/v for k,v in FILES.items()}
    tests=sorted(VOLUME.glob('regression_404_674_*.json'),key=lambda p:p.stat().st_mtime)
    if not tests:raise ValueError('regression_receipt_missing')
    paths['regression']=tests[-1];data={};hashes={}
    for key,path in paths.items():
        if path.is_symlink() or not path.is_file():raise ValueError('evidence_missing')
        raw=path.read_bytes();data[key]=json.loads(raw);hashes[key]=hashlib.sha256(raw).hexdigest()
    plan=json.loads((ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json').read_text())
    entries={str(x['version_id']):x for x in plan['entries'] if x['version_id'] in (404,674)}
    data['pdf_hashes']={k:hashlib.sha256(Path(v['local_sha_verified_paths'][0]).read_bytes()).hexdigest() for k,v in entries.items()}
    suites=('tests.test_docent_bag_404_postgres','tests.test_difficult_joint_postgres','tests.test_mixed_reconciliation_postgres')
    data['test_hashes']={name:hashlib.sha256((ROOT/Path(name.replace('.','/')+'.py')).read_bytes()).hexdigest() for name in suites}
    report=evaluate(data);report.update(created_utc=datetime.now(timezone.utc).isoformat(),evidence_sha256=hashes)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-name',default='reconciliation_404_674_final.json')
    name=parser.parse_args().output_name
    if not name or Path(name).name!=name or not name.endswith('.json'):raise ValueError('output_name_invalid')
    path=VOLUME/name
    if os.path.lexists(path):raise ValueError('receipt_exists')
    tmp=VOLUME/('.404_674_receipt_'+uuid.uuid4().hex)
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.link(tmp,path,follow_symlinks=False);tmp.unlink()
    print(json.dumps({'status':report['status'],'pending':report['pending'],'receipt':str(path)}));return 0 if not report['pending'] else 2

if __name__=='__main__':sys.exit(main())

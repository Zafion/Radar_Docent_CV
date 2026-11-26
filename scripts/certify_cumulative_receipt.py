"""Aggregate existing isolated-trial evidence without exposing row contents."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.restore_baseline_isolated import validate_volatile_storage

REQUIRED=('baseline','sources','first','quarantine','replay','execution','verification','observation','synthetic','hash_checks')


def evaluate(data):
    missing=[key for key in REQUIRED if key not in data]
    if missing:return {'status':'partial','pending':['missing_evidence:'+key for key in missing]}
    pending=[]
    def check(value,name):
        if not value:pending.append(name)
    baseline=data['baseline'];sources=data['sources'];first=data['first'].get('report',{});q=data['quarantine'].get('report',{})
    replay=data['replay'].get('report',{});execution=data['execution'];verification=data['verification'];obs=data['observation'];synthetic=data['synthetic']
    check(baseline.get('status')=='BASELINE_RESTORED' and baseline.get('source_sha256')=='4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b','baseline_identity')
    h=data['hash_checks']
    check(h.get('baseline')=='4d33c3fc07b5d6883bae8441ca1b0b9ed740a486bca237df278c4d4b69af198b' and h.get('reconstruction')=='b00de3a8818c2350ec314d0498320517adc7869eb6c16b198ffe5e1d54d9674a','source_dump_hashes')
    check(sources.get('status')=='SOURCES_PREPARED' and sources.get('source_document_identities')==9 and sources.get('source_award_results',0)>0,'source_identity')
    cluster=baseline.get('work_directory')
    check(bool(cluster) and all(data[k].get('cluster')==cluster for k in ('sources','execution','verification','observation')),'cluster_identity')
    keys=('700','436','701','expert','1000067','1000444')
    check(all(first.get('documents',{}).get(k,{}).get('status')=='applied' for k in keys),'six_documents_applied')
    check(all(replay.get('documents',{}).get(k,{}).get('status')=='applied' for k in keys),'six_documents_replayed')
    first_counts={'700':(6314,90),'436':(14439,2569),'701':(2,0),'1000067':(6028,124),'1000444':(10659,111)}
    check(all((first.get('documents',{}).get(k,{}).get('result',{}).get('inserted',0),
               first.get('documents',{}).get(k,{}).get('result',{}).get('awards_inserted',0))==v for k,v in first_counts.items())
          and first.get('documents',{}).get('expert',{}).get('result',{}).get('vacancies_inserted')==6,'first_execution_operations')
    inserted=('inserted','awards_inserted','associations_inserted','documents_inserted','vacancies_inserted','procedures_inserted')
    check(all(not any(replay.get('documents',{}).get(k,{}).get('result',{}).get(field,0) for field in inserted) for k in keys),'replay_insertions')
    check(len(q.get('quarantine',[]))==2 and sum(x.get('result',{}).get('inserted',0) for x in q.get('quarantine',[]))==598,'quarantine_recorded')
    check(len(replay.get('quarantine',[]))==2 and sum(x.get('result',{}).get('reused',0) for x in replay.get('quarantine',[]))==598 and all(x.get('result',{}).get('inserted',0)==0 for x in replay.get('quarantine',[])),'quarantine_replay')
    check(execution.get('status')=='COORDINATOR_REPLAY_CHECK' and execution.get('counts_idempotent') is True,'execution_replay')
    names=h.get('receipt_names',{})
    check(bool(names.get('quarantine')) and Path(execution.get('first',{}).get('receipt','')).name==names['quarantine'] and
          bool(names.get('replay')) and Path(execution.get('second',{}).get('receipt','')).name==names['replay'],'execution_receipt_links')
    check(verification.get('status')=='VERIFY_PASS' and verification.get('matches_700')==93 and verification.get('matches_701')==120 and verification.get('matches_19nov')==0 and verification.get('offer_quarantine_rows')==598,'verified_associations_and_quarantine')
    check(verification.get('rollback')=='PASS' and verification.get('unvalidated_constraints')==0,'rollback_and_constraints')
    check(synthetic.get('exit_code')==0 and synthetic.get('suite')=='tests.test_historical_cumulative_real_adapters','synthetic_rollback')
    check(synthetic.get('test_sha256')==h.get('synthetic_test_sha256') and bool(h.get('synthetic_test_sha256')),'synthetic_test_identity')
    check(obs.get('status')=='OBSERVATION_COMPLETE' and obs.get('document_identities')=={'n':778,'sha_count':778,'missing_sha':0},'document_sha_identities')
    comparisons=obs.get('baseline_comparisons',{})
    required_tables=('documents','document_versions','award_results','award_assignments','offered_positions','centers','docent_bag_members','difficult_coverage_positions','difficult_coverage_candidates','public_alerts','notification_posts','push_notification_events')
    check(all(t in comparisons and comparisons[t].get('baseline_rows_missing')==0 and comparisons[t].get('baseline_rows_changed')==0 for t in required_tables),'baseline_row_preservation')
    all_tables=obs.get('baseline_tables',[])
    check(bool(all_tables) and not obs.get('baseline_tables_without_primary_key') and set(all_tables)==set(comparisons) and
          all(v.get('baseline_rows_missing')==0 and v.get('baseline_rows_changed')==0 for v in comparisons.values()),'all_baseline_tables_preserved')
    totals=obs.get('totals',{})
    check(all(totals.get(k)==v for k,v in {'documents':778,'award_results':593050,'award_assignments':47966,'public_alerts':194,'notification_posts':776,'push_notification_events':204}.items()),'current_totals')
    check(obs.get('experts')=={'n':6,'unawarded':6},'expert_vacancies')
    expected={'700':(6416,93),'436':(17051,5116),'701':(11024,120),'1000067':(6028,124),'1000444':(10659,111)}
    documents=obs.get('documents',{})
    check(all(documents.get(k,{}).get('documents')==1 and (documents[k].get('results'),documents[k].get('assignments'))==v for k,v in expected.items()) and documents.get('1000391',{}).get('documents')==1,'per_document_counts')
    check(all(h.get('documents',{}).get(k)==documents.get(k,{}).get('sha256') and h.get('documents',{}).get(k) for k in (*expected,'1000391')),'document_pdf_hashes')
    links={x['procedure_key']:x['n'] for x in verification.get('procedure_documents',[])}
    check(all(links.get(k)==v for k,v in {'interino-2026-11-03':3,'expertos-2026-11-05':1,'interino-2026-11-19':4}.items()),'procedure_links')
    reasons={x['reason']:x['n'] for x in verification.get('review_quarantine',[])}
    check(reasons.get('raw_block_conflict_unresolved')==1 and reasons.get('target_association_without_source_evidence')==2547,'historical_review_quarantines')
    return {'status':'partial' if pending else 'complete','pending':pending,'document_counts':documents,'totals':totals,
            'units':{k:{'first':first.get('documents',{}).get(k),'replay':replay.get('documents',{}).get(k)} for k in keys},
            'idempotence':'PASS' if 'replay_insertions' not in pending and 'quarantine_replay' not in pending and 'execution_replay' not in pending else 'PENDING',
            'referential_integrity':{'unvalidated_constraints':verification.get('unvalidated_constraints')},
            'notifications':{k:comparisons.get(k) for k in ('public_alerts','notification_posts','push_notification_events')},
            'outside_scope':{'prior_candidate_difference_results':111,'prior_candidate_difference_assignments':111,'documentary_identity':'not_compared'},
            'associations':{'maestros_03nov':93,'secondary_03nov':120,'nov19_speculative':0},
            'quarantine':{'nov19_positions':598,'ordinal99':1,'maestros436_associations':2547},
            'rollback':{'real_quarantine':verification.get('rollback'),'synthetic_awards':synthetic.get('exit_code')==0,
                        'real_award_rollback':'not_tested'},'baseline_comparisons':comparisons}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-root',required=True,type=Path)
    for name in REQUIRED:
        if name!='hash_checks':parser.add_argument('--'+name,required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();os.environ['FUNK_RECOVERY_WORK_ROOT']=str(args.work_root)
    if validate_volatile_storage(minimum_bytes=0)!=args.work_root:raise ValueError('work_volume_invalid')
    if args.output.parent!=args.work_root or os.path.lexists(args.output):raise ValueError('output_must_be_new_and_in_volume')
    data={};hashes={}
    for name in REQUIRED:
        if name=='hash_checks':continue
        path=getattr(args,name)
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(args.work_root):raise ValueError('evidence_path_invalid')
        raw=path.read_bytes();data[name]=json.loads(raw);hashes[name]=hashlib.sha256(raw).hexdigest()
    def digest(path):
        h=hashlib.sha256()
        with path.open('rb') as f:
            while chunk:=f.read(1024*1024):h.update(chunk)
        return h.hexdigest()
    plan=ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/plan_integral_reanudable.json'
    entries={str(x['version_id']):x for x in json.loads(plan.read_text())['entries']}
    versions=('700','436','701','1000391','1000067','1000444')
    data['hash_checks']={'baseline':digest(ROOT/'auditoria_local/recuperacion_17nov_luks/baseline_21nov_20261124/baseline.dump'),
        'reconstruction':digest(ROOT/'auditoria_local/resultados/reanudacion_publicaciones_20261117/reprocesado_corpus_integral_20261120/backup_antes_etiquetas_20261123/historical.dump'),
        'documents':{k:digest(Path(entries[k]['local_sha_verified_paths'][0])) for k in versions},
        'receipt_names':{name:getattr(args,name).name for name in REQUIRED if name!='hash_checks'},
        'synthetic_test_sha256':digest(ROOT/'tests/test_historical_cumulative_real_adapters.py')}
    report=evaluate(data);report.update(created_utc=datetime.now(timezone.utc).isoformat(),evidence_sha256=hashes,source_sha256=data['hash_checks'])
    temp=args.work_root/('.aggregate_'+uuid.uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(report,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.link(temp,args.output,follow_symlinks=False);temp.unlink()
    print(json.dumps({'status':report['status'],'pending':report['pending'],'receipt':str(args.output)}))
    return 0 if report['status']=='complete' else 2


if __name__=='__main__':sys.exit(main())

"""Replay local historical candidates in an owned disposable PostgreSQL 16 cluster.

This tool cannot connect to an existing database and does not publish results.
Its JSON report contains aggregate information only, never parsed person data.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import signal
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests.postgres_isolated_validation import FreshCluster, connection_guards
from app.services.document_classifier import DocumentClassifierService
from app.services.parse_plan import preview
from app.services.parse_selection import selection_from_plan, selection_scope
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
from app.services.final_award_listing_maestros_parser import STATUS_VALUES
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from app.services.career_practice_award_parser import is_career_layout
from app.services.offered_positions_parser import OfferedPositionsParserService
from app.services.difficult_coverage_provisional_parser import DifficultCoverageProvisionalParserService
from app.services.docent_bag_participants_parser import DocentBagParticipantsParserService
import psycopg
from psycopg.rows import dict_row


class NonEmptyBagParser(DocentBagParticipantsParserService):
    """Keep the original extraction and reject empty results before deletion."""
    def _parse_document(self, document):
        rows = super()._parse_document(document)
        if not rows:
            raise ValueError('empty_extraction_rejected')
        return rows


class CheckedSecondaryParser(FinalAwardListingSecundariaParserService):
    """Reject incomplete interino status extraction before replacement."""
    def _parse_document(self, document):
        rows = super()._parse_document(document)
        if not rows:
            raise ValueError('empty_extraction_rejected')
        lines = self._extract_clean_lines(self._resolve_file_path(document['file_path']))
        if not is_career_layout(lines):
            expected = Counter(line for line in lines if line in STATUS_VALUES)
            actual = Counter(row.status for row in rows)
            if not expected or expected != actual:
                raise ValueError('secondary_status_counts_mismatch')
        return rows


PARSERS = {
    ('final_award_listing', 'maestros'): ('final_award_listing_maestros_parser', FinalAwardListingMaestrosParserService),
    ('final_award_listing', 'secundaria_otros'): ('final_award_listing_secundaria_parser', CheckedSecondaryParser),
    ('offered_positions', None): ('offered_positions_parser', OfferedPositionsParserService),
    ('difficult_coverage_provisional', None): ('difficult_coverage_provisional_parser', DifficultCoverageProvisionalParserService),
    ('docent_bag_participants', None): ('docent_bag_participants_parser', NonEmptyBagParser),
    ('docent_career_practice_participants', None): ('docent_bag_participants_parser', NonEmptyBagParser),
}


def sha_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): digest.update(block)
    return digest.hexdigest()


def save(path, report):
    temporary = path.with_suffix('.tmp')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        json.dump(report, f, ensure_ascii=False, indent=2); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)


def classify(entry, path):
    asset = (entry.get('sources') or [{}])[0]
    return DocumentClassifierService().classify(
        file_path=str(path), original_filename=entry.get('original_filename_hint') or path.name, asset_title=entry.get('title_hint'), asset_role='',
        source_key=asset.get('source_key') or '', source_label=None, section=None,
        publication_label=None, publication_date_text=None)


def run(plan_path, report_path, limit=None, resume=False, persistent_dir=None, cluster_binding_plan=None, replay_existing=False):
    raw = Path(plan_path).read_bytes()
    plan = json.loads(raw)
    if plan.get('format') != 'funkcionario.historical-reprocess-plan.v1': raise ValueError('wrong_plan')
    report = {'plan_sha256': hashlib.sha256(raw).hexdigest(), 'phase': 'running',
              'comparison': 'comparison_unavailable', 'rows': [], 'cluster': 'postgresql16_temporary'}
    report_path = Path(report_path)
    finished = set()
    if resume:
        previous=json.loads(report_path.read_text())
        if previous.get('plan_sha256') != report['plan_sha256'] or previous.get('phase')=='completed':
            raise ValueError('invalid_resume_report')
        report['rows']=[row for row in previous.get('rows',[]) if row.get('status') in ('processed','failed','skipped')]
        finished={row['version_id'] for row in report['rows']}
        if not persistent_dir: report['resume_limit']='Fresh cluster; previous database rows are not retained and cross-segment matching is unavailable.'
    elif report_path.exists():
        raise ValueError('report_already_exists')
    save(report_path, report)
    if persistent_dir:
        from scripts.historical_persistent_cluster import HistoricalCluster
        binding_sha=hashlib.sha256(Path(cluster_binding_plan).read_bytes()).hexdigest() if cluster_binding_plan else report['plan_sha256']
        cluster_context=HistoricalCluster(persistent_dir,binding_sha)
    else: cluster_context=FreshCluster()
    with cluster_context as cluster:
        with psycopg.connect(cluster.dsn('postgres'), autocommit=True) as admin:
            cluster.verify(admin)
            created_database=not bool(admin.execute("SELECT 1 FROM pg_database WHERE datname='funk_validation'").fetchone())
            if created_database: admin.execute('CREATE DATABASE funk_validation')
        env = {k:v for k,v in os.environ.items() if not k.startswith(('PG','RADAR_','DATABASE_','NOTIFICATION_','TELEGRAM_'))}
        env.update(RADAR_DOCENT_DB_URL=cluster.dsn(), ENABLE_NOTIFICATIONS='0')
        with patch.dict(os.environ, env, clear=True), ExitStack() as stack:
            list(connection_guards(cluster, stack))
            stack.enter_context(patch('app.services.assignment_matcher.AssignmentMatcherService.match_all', side_effect=AssertionError('matcher_forbidden_in_replay')))
            stack.enter_context(patch('app.services.notification_eligibility.reconcile_if_configured', side_effect=AssertionError('reconciliation_forbidden')))
            from app.storage.db import init_db, close_pool
            stack.callback(close_pool)
            if created_database: init_db(str(ROOT/'app/storage/schema.sql'))
            with psycopg.connect(cluster.dsn(), row_factory=dict_row) as db:
                found=db.execute("SELECT id FROM sources WHERE source_key='local-historical-test'").fetchone()
                sid=(found or db.execute("INSERT INTO sources(source_key,source_url,label) VALUES('local-historical-test','https://example.invalid','local isolated') RETURNING id").fetchone())['id']
                db.commit()
                completed_this_session=0
                for entry in plan['entries']:
                    if entry['version_id'] in finished:
                        continue
                    if limit is not None and completed_this_session >= limit: break
                    completed_this_session+=1
                    item={'version_id':entry['version_id'], 'sha256':entry['sha256'],
                          'original_family':entry['family'], 'period':entry['period_status'],
                          'comparison':'comparison_unavailable'}
                    report['rows'].append(item)
                    try:
                        paths=entry.get('local_sha_verified_paths') or []
                        if not paths:
                            item.update(status='skipped', reason='pdf_not_available_locally'); continue
                        path=Path(paths[0])
                        if path.is_symlink() or not path.is_file() or sha_file(path)!=entry['sha256']:
                            item.update(status='skipped',reason='pdf_identity_mismatch'); continue
                        if entry.get('format') == 'other':
                            item.update(status='skipped', reason='unsupported_non_pdf_format', classified_family='unsupported_format'); continue
                        c=classify(entry,path)
                        family,scope=c.doc_family,c.list_scope
                        candidate=entry.get('isolated_content_candidate')
                        if candidate and family in ('unknown','ignored'):
                            families={'possible_offered_positions':'offered_positions','possible_difficult_positions':'offered_positions','possible_difficult_candidates':'difficult_coverage_provisional','possible_award_results':'final_award_listing','possible_bag_participants':'docent_bag_participants','possible_career_participants':'docent_career_practice_participants'}
                            if candidate not in families: raise ValueError('unsupported_content_candidate')
                            family=families[candidate];scope='dificil_cobertura' if candidate.startswith('possible_difficult') else entry.get('isolated_candidate_scope')
                            if family in ('final_award_listing','docent_bag_participants','docent_career_practice_participants') and scope not in ('maestros','secundaria_otros'):raise ValueError('candidate_scope_unproven')
                            item['candidate_content_classification']=candidate
                        item.update(classified_family=family,classified_scope=scope,classification_signals=c.signals)
                        if entry['family'] not in ('unknown','ignored','unregistered',family):
                            item.update(status='skipped',reason='classification_conflict'); continue
                        key=(family,scope) if family=='final_award_listing' else (family,None)
                        if key not in PARSERS:
                            item.update(status='skipped',reason='unsupported_or_ambiguous_family'); continue
                        if family=='docent_bag_participants' and scope not in ('maestros','secundaria_otros'):
                            item.update(status='skipped',reason='ambiguous_bag_scope'); continue
                        parser_key,service=PARSERS[key]
                        existing=db.execute('SELECT v.id AS vid,d.id AS did,d.doc_family,d.list_scope FROM document_versions v LEFT JOIN documents d ON d.document_version_id=v.id WHERE v.sha256=%s',(entry['sha256'],)).fetchone()
                        if existing:
                            if not replay_existing:
                                item.update(status='skipped',reason='duplicate_sha_already_present_requires_review');db.rollback();continue
                            if existing['did'] is None or existing['doc_family'] != family or existing['list_scope'] != scope:
                                item.update(status='skipped',reason='existing_identity_or_scope_mismatch');db.rollback();continue
                            vid,did=existing['vid'],existing['did'];db.rollback()
                        else:
                            vid=db.execute("INSERT INTO document_versions(sha256,original_filename,stored_filename,file_path,size_bytes,downloaded_at,notification_ingest_intent) VALUES(%s,%s,%s,%s,%s,now(),'unknown') RETURNING id",(entry['sha256'],path.name,path.name,str(path),path.stat().st_size)).fetchone()['id']
                            did=db.execute("INSERT INTO documents(document_version_id,source_id,doc_family,list_scope,title,document_date_iso) VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",(vid,sid,family,scope,'isolated historical',c.document_date_iso)).fetchone()['id']
                            if entry['previous_status']=='success':
                                # Prior state is synthetic: prior production rows are unavailable.
                                db.execute("INSERT INTO document_parse_runs(document_version_id,parser_key,parser_version,status,started_at) VALUES(%s,%s,'prior-state-only','success',now())",(vid,parser_key))
                            db.commit()
                        selected=preview(shas=[entry['sha256']],parser_key=parser_key,reprocess=True)
                        with selection_scope(selection_from_plan(selected)):
                            results=service().parse_all_documents()
                        if len(results)!=1:
                            item.update(status='failed',reason='unexpected_parser_result'); continue
                        outcome=results[0];status=outcome.get('status')
                        if status=='success' and (outcome.get('rows_extracted') or outcome.get('candidates_extracted') or outcome.get('members_extracted') or 0)>0:
                            item.update(status='processed', parser=parser_key, rows=outcome.get('rows_extracted') or outcome.get('candidates_extracted') or outcome.get('members_extracted'),
                                        validation='parser_success_completeness_review_required')
                            db.rollback()
                            if family=='final_award_listing':
                                item['states']={r['status']:r['n'] for r in db.execute('SELECT status,count(*) n FROM award_results WHERE document_id=%s GROUP BY status',(did,)).fetchall()}
                                item['assignments']=db.execute('SELECT count(*) n FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s',(did,)).fetchone()['n']
                                item['validation']='parser_validated' if scope=='maestros' else 'parser_success_completeness_review_required'
                            elif family=='offered_positions':
                                item['essential_incomplete']=db.execute('SELECT count(*) n FROM offered_positions WHERE document_id=%s AND (position_code IS NULL OR center_code IS NULL OR specialty_code IS NULL)',(did,)).fetchone()['n']
                        else:
                            safe_reason=outcome.get('reason')
                            if outcome.get('error')=='empty_extraction_rejected':
                                safe_reason='empty_extraction_rejected'
                            elif outcome.get('error')=='secondary_status_counts_mismatch':
                                safe_reason='secondary_status_counts_mismatch'
                            elif outcome.get('error')=='Offered positions heading recognized but no rows parsed':
                                safe_reason='offered_heading_without_parsed_rows'
                            if safe_reason not in ('unsupported_format','explicitly_empty','empty_extraction_rejected','offered_heading_without_parsed_rows','secondary_status_counts_mismatch'):
                                safe_reason='parser_'+str(status)
                            item.update(status='skipped' if status=='skipped' else 'failed',reason=safe_reason,parser=parser_key)
                            error_text=str(outcome.get('error') or '')
                            allowed=('offered_unrecognized_numbered_row','offered_unrecognized_center_line','offered_extraction_incomplete','No readable text in offered positions document','Offered position rows recognized but no rows parsed','Offered positions heading recognized but no rows parsed','No rows parsed from offered positions document','No results parsed from secundaria award document','No results parsed from maestros award document','No readable text in maestros award document','empty_extraction_rejected','secondary_status_counts_mismatch','career_participants_extraction_incomplete','expert_table_geometry_unsupported','expert_table_boundaries_missing','expert_table_row_incomplete_or_ambiguous','expert_table_page_without_rows','expert_table_no_rows','conflicting_itinerancy_fields')
                            item['diagnostic_code']=next((code for code in allowed if error_text.startswith(code)), 'other_redacted' if error_text else None)
                            item['diagnostic_sha256']=hashlib.sha256(error_text.encode()).hexdigest() if error_text else None
                        db.rollback()
                    except Exception as exc:
                        db.rollback();item.update(status='failed',reason=type(exc).__name__)
                    finally:
                        save(report_path,report)
                for table in ('public_alerts','notification_posts','push_notification_events'):
                    if db.execute('SELECT count(*) AS n FROM '+table).fetchone()['n']:
                        raise RuntimeError('unexpected_notification_rows')
    report['phase']='completed' if len(report['rows']) == len(plan['entries']) else 'paused'; report['counts']=dict(Counter(x.get('status','unfinished') for x in report['rows']))
    report['family_counts']={family:dict(Counter(x.get('status','unfinished') for x in report['rows'] if x.get('classified_family',x['original_family'])==family)) for family in sorted({x.get('classified_family',x['original_family']) for x in report['rows']})}
    report['matcher']='not_run';report['cluster_cleaned']=not bool(persistent_dir);report['cluster_retained']=bool(persistent_dir)
    save(report_path,report)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--plan',required=True);parser.add_argument('--report',required=True);parser.add_argument('--limit',type=int);parser.add_argument('--resume',action='store_true');parser.add_argument('--persistent-cluster');parser.add_argument('--cluster-binding-plan');parser.add_argument('--replay-existing',action='store_true')
    args=parser.parse_args();os.umask(0o077)
    def stop_gracefully(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, stop_gracefully)
    result=run(args.plan,args.report,args.limit,args.resume,args.persistent_cluster,args.cluster_binding_plan,args.replay_existing)
    print(json.dumps({'phase':result['phase'],'counts':result['counts'],'family_counts':result['family_counts']}))

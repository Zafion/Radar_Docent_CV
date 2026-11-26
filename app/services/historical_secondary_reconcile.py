"""Document-scoped, multiset-safe secondary award reconciliation in a trial.

Secondary ordinals repeat across specialties. Complete source blocks, not
ordinal or name alone, are used to recognise existing records. Identical
source duplicates remain separate and are never paired arbitrarily.
"""
from collections import Counter, defaultdict
import hashlib
from pathlib import Path
from urllib.parse import urlsplit

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from app.services.discovery.verified_evidence import verify_capture
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from app.services.historical_batch_reconcile import copy_fields
from scripts.integrate_03nov_trial import insert_row

AWARD_FIELDS=('assignment_kind','locality','center_code','center_name','position_specialty_code',
              'position_specialty_name','position_code','hours_text','hours_value','petition_text',
              'petition_number','request_type','raw_assignment_text')


def _trial_fence(db, token):
    info=db.execute("SELECT current_database() AS name,current_setting('data_directory') AS directory,"
                    "current_setting('listen_addresses') AS listen,current_setting('server_version_num') AS version,"
                    "current_setting('funkcionario.validation_token',true) AS token").fetchone()
    synthetic=bool(token and info['name']=='funk_validation' and info['token']==token)
    isolated=info['name'] in ('baseline_secondary_cumulative','baseline_700_full_trial_v2','baseline_19nov_trial','baseline_future_trial') and info['directory']=='/work/data'
    if info['listen'] or not 160000<=int(info['version'])<170000 or not (synthetic or isolated):
        raise ValueError('destination_not_authorized_trial')


def reconcile_secondary(source,destination,*,document_sha,pdf_path,synthetic_token=None,provenance=None):
    """Recover safe document rows and awards, never creating offer links."""
    if source.info.transaction_status!=TransactionStatus.IDLE or destination.info.transaction_status!=TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    path=Path(pdf_path)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=document_sha:
        raise ValueError('pdf_identity_mismatch')
    parsed=FinalAwardListingSecundariaParserService()._parse_document({'file_path':str(path),'list_scope':'secundaria_otros'})
    if not parsed:raise ValueError('empty_extraction')
    source.row_factory=destination.row_factory=dict_row
    with source.transaction(),destination.transaction():
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        destination.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        _trial_fence(destination,synthetic_token)
        docs=source.execute('''SELECT d.* FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(document_sha,)).fetchall()
        targets=destination.execute('''SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s''',(document_sha,)).fetchall()
        if len(docs)!=1 or len(targets)>1:raise ValueError('document_identity_ambiguous')
        doc=docs[0];sid=doc['id']
        rows=source.execute('SELECT * FROM award_results WHERE document_id=%s',(sid,)).fetchall()
        pdf_counts=Counter((r.order_number,r.status,r.raw_block_text,r.person_display_name,r.person_name_normalized,r.specialty_code,r.body_name) for r in parsed)
        db_counts=Counter((r['order_number'],r['status'],r['raw_block_text'],r['person_display_name'],r['person_name_normalized'],r['specialty_code'],r['body_name']) for r in rows)
        if pdf_counts!=db_counts or any(r['list_scope']!='secundaria_otros' for r in rows):raise ValueError('pdf_result_mismatch')
        awards=source.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''',(sid,)).fetchall()
        by_id={r['id']:r for r in rows}
        source_awards=Counter((by_id[a['award_result_id']]['raw_block_text'],tuple(a[k] for k in AWARD_FIELDS)) for a in awards)
        parsed_awards=Counter((r.raw_block_text,tuple(getattr(r.assignment,k) for k in AWARD_FIELDS)) for r in parsed if r.assignment is not None)
        if source_awards!=parsed_awards:raise ValueError('pdf_assignment_mismatch')
        created=False
        if not targets:
            if not provenance:raise ValueError('missing_document_provenance')
            verify_capture(html_bytes=provenance['html_bytes'],expected_html_sha256=provenance['html_sha256'],
                final_page_url=provenance['page_url'],linked_url=provenance['document_url'],pdf_bytes=path.read_bytes(),expected_pdf_sha256=document_sha)
            for key in ('page_url','document_url'):
                url=urlsplit(provenance[key])
                if url.scheme!='https' or url.hostname!='ceice.gva.es' or url.username or url.password:raise ValueError('official_provenance_required')
            if not urlsplit(provenance['document_url']).path.startswith('/documents/'):raise ValueError('official_document_path_required')
            roots=destination.execute("SELECT id,source_url FROM sources WHERE source_key='rrhh_main'").fetchall()
            if len(roots)!=1 or roots[0]['source_url']!='https://ceice.gva.es/es/web/rrhh-educacion':raise ValueError('official_source_missing')
            version=source.execute('SELECT * FROM document_versions WHERE id=%s',(doc['document_version_id'],)).fetchone()
            run=source.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(version['id'],)).fetchone()
            if not run or run['rows_extracted']!=len(rows):raise ValueError('source_parse_run_not_complete')
            data={k:version[k] for k in copy_fields(source,destination,'document_versions')}
            if 'notification_ingest_intent' in data:data['notification_ingest_intent']='unknown'
            vid=insert_row(destination,'document_versions',data)
            pdata={k:run[k] for k in copy_fields(source,destination,'document_parse_runs')};pdata['document_version_id']=vid
            pid=insert_row(destination,'document_parse_runs',pdata)
            ddata={k:doc[k] for k in copy_fields(source,destination,'documents')}
            ddata.update(document_version_id=vid,source_id=roots[0]['id'],validation_status='needs_validation',validation_parse_run_id=pid,historical_status='unknown',publication_date_quality='unknown')
            did=insert_row(destination,'documents',ddata);created=True
        else:did=targets[0]['id']
        old=destination.execute('SELECT * FROM award_results WHERE document_id=%s',(did,)).fetchall()
        old_awards=destination.execute('''SELECT a.* FROM award_assignments a JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''',(did,)).fetchall()
        fields=copy_fields(source,destination,'award_results')
        compare=tuple(k for k in fields if k not in ('document_id','created_at'))
        if not {'raw_block_text','order_number','status','person_name_normalized','specialty_code','list_scope'}<=set(compare):raise ValueError('result_identity_fields_missing')
        src_groups=defaultdict(list);dst_groups=defaultdict(list)
        for r in rows:src_groups[r['raw_block_text']].append(r)
        for r in old:dst_groups[r['raw_block_text']].append(r)
        destination.execute('''CREATE SCHEMA IF NOT EXISTS recovery_secondary;
          CREATE TABLE IF NOT EXISTS recovery_secondary.quarantine(document_sha text NOT NULL,block_sha text NOT NULL,reason text NOT NULL,PRIMARY KEY(document_sha,block_sha,reason));
          CREATE TABLE IF NOT EXISTS recovery_secondary.document_evidence(document_sha text PRIMARY KEY,document_id bigint NOT NULL REFERENCES documents(id),html_sha256 text NOT NULL,page_url text NOT NULL,document_url text NOT NULL)''')
        if created:
            destination.execute('INSERT INTO recovery_secondary.document_evidence VALUES(%s,%s,%s,%s,%s)',
                                (document_sha,did,provenance['html_sha256'],provenance['page_url'],provenance['document_url']))
        stats=Counter();mapped={};source_by_result=defaultdict(list);target_by_result=defaultdict(list)
        for award in awards:source_by_result[award['award_result_id']].append(award)
        for award in old_awards:target_by_result[award['award_result_id']].append(award)
        # A target-only or changed block can be an alternative representation
        # of an apparent missing source row; never insert until reviewed.
        target_conflict=any(block not in src_groups or Counter(tuple(r[k] for k in compare) for r in group)-Counter(tuple(r[k] for k in compare) for r in src_groups[block]) for block,group in dst_groups.items())
        def quarantine(block,reason):
            if not block:raise ValueError('empty_source_block')
            block_sha=hashlib.sha256(block.encode()).hexdigest()
            destination.execute('INSERT INTO recovery_secondary.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',(document_sha,block_sha,reason))
        for block,group in src_groups.items():
            prior=dst_groups.get(block,[])
            if not block:raise ValueError('empty_source_block')
            if prior:
                source_fields=Counter(tuple(r[k] for k in compare) for r in group)
                target_fields=Counter(tuple(r[k] for k in compare) for r in prior)
                if len(group)==1 and len(prior)==1 and source_fields==target_fields:
                    mapped[group[0]['id']]=prior[0]['id'];stats['reused']+=1
                elif len(group)==len(prior) and source_fields==target_fields:
                    stats['reused_duplicate_rows']+=len(group)
                    if any(source_by_result[r['id']] for r in group):
                        quarantine(block,'duplicate_award_identity_ambiguous');stats['duplicate_award_groups_pending']+=1
                elif (len(group)>1 and len(prior)<len(group) and not target_conflict
                      and not (target_fields-source_fields)
                      and not any(source_by_result[r['id']] for r in group)
                      and not any(target_by_result[r['id']] for r in prior)):
                    # The source PDF establishes multiplicity; no individual
                    # award or person-to-destination pairing is attempted.
                    missing=source_fields-target_fields
                    stats['reused_duplicate_rows']+=len(prior)
                    for row in group:
                        key=tuple(row[k] for k in compare)
                        if missing[key]:
                            data={k:row[k] for k in fields};data['document_id']=did
                            insert_row(destination,'award_results',data);missing[key]-=1;stats['inserted']+=1
                else:
                    quarantine(block,'block_multiplicity_or_fields_conflict');stats['quarantined']+=len(group)
                continue
            if not prior and len(group)>1 and not target_conflict and not any(source_by_result[r['id']] for r in group):
                for row in group:
                    data={k:row[k] for k in fields};data['document_id']=did
                    insert_row(destination,'award_results',data);stats['inserted']+=1
                continue
            if target_conflict or len(group)!=1:
                quarantine(block,'target_unmatched_or_duplicate_source');stats['quarantined']+=len(group);continue
            row=group[0];data={k:row[k] for k in fields};data['document_id']=did
            mapped[row['id']]=insert_row(destination,'award_results',data);stats['inserted']+=1
        afields=copy_fields(source,destination,'award_assignments')
        for award in awards:
            src_result=by_id[award['award_result_id']];target_id=mapped.get(src_result['id'])
            if target_id is None:
                stats['awards_pending']+=1;continue
            existing=target_by_result.get(target_id,[])
            if existing:
                if len(existing)==1 and len(source_by_result[src_result['id']])==1 and all(existing[0][k]==award[k] for k in AWARD_FIELDS):
                    stats['awards_reused']+=1
                    if existing[0].get('matched_offered_position_id') is not None:stats['existing_links_preserved']+=1
                    elif award.get('matched_offered_position_id') is not None:
                        quarantine(src_result['raw_block_text'],'offer_association_requires_evidence');stats['links_pending']+=1
                else:quarantine(src_result['raw_block_text'],'award_fields_conflict');stats['awards_pending']+=1
                continue
            if len(source_by_result[src_result['id']])!=1:
                quarantine(src_result['raw_block_text'],'multiple_source_awards');stats['awards_pending']+=1;continue
            data={k:award[k] for k in afields};data['award_result_id']=target_id;data['matched_offered_position_id']=None
            if award.get('matched_offered_position_id') is not None:
                quarantine(src_result['raw_block_text'],'offer_association_requires_evidence');stats['links_pending']+=1
            insert_row(destination,'award_assignments',data);stats['awards_inserted']+=1
        stats['source_rows']=len(rows);stats['target_only_blocks']=sum(1 for block in dst_groups if block not in src_groups)
        if sum(stats[k] for k in ('reused','reused_duplicate_rows','inserted','quarantined'))!=len(rows):
            raise ValueError('result_multiset_accounting_mismatch')
        if sum(stats[k] for k in ('awards_reused','awards_inserted','awards_pending'))!=len(awards):
            raise ValueError('award_multiset_accounting_mismatch')
        return dict(stats)

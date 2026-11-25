"""Conservatively reconcile results from an already known document.

The default remains limited to unawarded results. An explicit opt-in also
reconciles PDF-checked awards and independently accredited offer links.
It cannot complete a procedure on its own; ambiguous identity stays in quarantine.
"""
from collections import Counter
import hashlib
from pathlib import Path

from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from urllib.parse import urlsplit
from app.services.historical_batch_reconcile import copy_fields
from app.services.discovery.verified_evidence import verify_capture
from app.services.final_award_listing_maestros_parser import FinalAwardListingMaestrosParserService
from app.services.final_award_listing_secundaria_parser import FinalAwardListingSecundariaParserService
from scripts.integrate_03nov_trial import insert_row


def _trial_fence(db, synthetic_token):
    info = db.execute("SELECT current_database() AS name,current_setting('data_directory') AS directory,"
                      "current_setting('listen_addresses') AS listen,current_setting('server_version_num') AS version,"
                      "current_setting('funkcionario.validation_token',true) AS token").fetchone()
    if info['listen'] or not 160000 <= int(info['version']) < 170000:
        raise ValueError('destination_not_isolated_postgres16')
    synthetic = bool(synthetic_token and info['name'] == 'funk_validation' and info['token'] == synthetic_token)
    isolated = info['directory'] == '/work/data' and info['name'] in (
        'baseline_future_trial', 'baseline_700_full_trial', 'baseline_700_rollback_trial',
        'baseline_700_full_trial_v2', 'baseline_700_rollback_trial_v2')
    if not (synthetic or isolated):
        raise ValueError('destination_not_authorized_trial')


def reconcile_unawarded(source, destination, *, document_sha, pdf_path, family, synthetic_token=None,
                        include_awards=False, accredited_offer_sha=None, provenance=None):
    """Reconcile checked rows; award and offer links require explicit opt-in."""
    if source.info.transaction_status != TransactionStatus.IDLE or destination.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    path = Path(pdf_path)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != document_sha:
        raise ValueError('pdf_identity_mismatch')
    parsers = {'maestros': FinalAwardListingMaestrosParserService,
               'secundaria_otros': FinalAwardListingSecundariaParserService}
    if family not in parsers:
        raise ValueError('unsupported_family')
    parsed = parsers[family]()._parse_document({'file_path': str(path), 'list_scope': family})
    if not parsed:
        raise ValueError('empty_extraction')
    source.row_factory = destination.row_factory = dict_row
    with source.transaction(), destination.transaction():
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        destination.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        _trial_fence(destination, synthetic_token)
        source_docs = source.execute('''SELECT d.* FROM documents d JOIN document_versions v
            ON v.id=d.document_version_id WHERE v.sha256=%s''', (document_sha,)).fetchall()
        target_docs = destination.execute('''SELECT d.id FROM documents d JOIN document_versions v
            ON v.id=d.document_version_id WHERE v.sha256=%s''', (document_sha,)).fetchall()
        if len(source_docs) != 1 or len(target_docs) > 1:
            raise ValueError('document_identity_missing_or_ambiguous')
        sid = source_docs[0]['id']
        rows = source.execute('SELECT * FROM award_results WHERE document_id=%s', (sid,)).fetchall()
        pdf_counter = Counter((r.order_number, r.status, r.raw_block_text, r.person_display_name,
                               r.person_name_normalized, r.specialty_code) for r in parsed)
        source_counter = Counter((r['order_number'], r['status'], r['raw_block_text'], r['person_display_name'],
                                  r['person_name_normalized'], r['specialty_code']) for r in rows)
        if pdf_counter != source_counter or any(r['list_scope'] != family for r in rows):
            raise ValueError('pdf_extraction_mismatch')
        if not target_docs:
            if not provenance:
                raise ValueError('missing_document_provenance')
            verify_capture(html_bytes=provenance['html_bytes'], expected_html_sha256=provenance['html_sha256'],
                           final_page_url=provenance['page_url'], linked_url=provenance['document_url'],
                           pdf_bytes=path.read_bytes(), expected_pdf_sha256=document_sha)
            for key in ('page_url', 'document_url'):
                parsed_url = urlsplit(provenance[key])
                if parsed_url.scheme != 'https' or parsed_url.hostname != 'ceice.gva.es' or parsed_url.username or parsed_url.password:
                    raise ValueError('official_provenance_required')
            if not urlsplit(provenance['document_url']).path.startswith('/documents/'):
                raise ValueError('official_document_path_required')
            dest_source = destination.execute("SELECT id,source_url FROM sources WHERE source_key='rrhh_main'").fetchall()
            if len(dest_source) != 1 or dest_source[0]['source_url'] != 'https://ceice.gva.es/es/web/rrhh-educacion':
                raise ValueError('official_source_missing')
            version = source.execute('SELECT * FROM document_versions WHERE id=%s', (source_docs[0]['document_version_id'],)).fetchone()
            parse_run = source.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1", (version['id'],)).fetchone()
            if not parse_run or parse_run['rows_extracted'] != len(rows):
                raise ValueError('source_parse_run_not_complete')
            vdata = {k: version[k] for k in copy_fields(source, destination, 'document_versions')}
            if 'notification_ingest_intent' in vdata:
                vdata['notification_ingest_intent'] = 'unknown'
            vid = insert_row(destination, 'document_versions', vdata)
            pdata = {k: parse_run[k] for k in copy_fields(source, destination, 'document_parse_runs')}
            pdata['document_version_id'] = vid
            pid = insert_row(destination, 'document_parse_runs', pdata)
            ddata = {k: source_docs[0][k] for k in copy_fields(source, destination, 'documents')}
            ddata.update(document_version_id=vid, source_id=dest_source[0]['id'], validation_status='needs_validation',
                         validation_parse_run_id=pid, historical_status='unknown', publication_date_quality='unknown')
            did = insert_row(destination, 'documents', ddata)
        else:
            did = target_docs[0]['id']
        old = destination.execute('SELECT * FROM award_results WHERE document_id=%s', (did,)).fetchall()
        source_counts = Counter(r['order_number'] for r in rows)
        target_counts = Counter(r['order_number'] for r in old)
        source_awards = source.execute('''SELECT a.* FROM award_assignments a
            JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''', (sid,)).fetchall()
        target_awards = destination.execute('''SELECT a.* FROM award_assignments a
            JOIN award_results r ON r.id=a.award_result_id WHERE r.document_id=%s''', (did,)).fetchall()
        source_assignments = {r['award_result_id'] for r in source_awards}
        target_assignments = {r['award_result_id'] for r in target_awards}
        if include_awards:
            award_fields = ('assignment_kind','locality','center_code','center_name','position_specialty_code',
                            'position_specialty_name','position_code','hours_text','hours_value','petition_text',
                            'petition_number','request_type','raw_assignment_text')
            order_by_id = {row['id']: row['order_number'] for row in rows}
            parsed_awards = Counter((r.order_number, tuple(getattr(r.assignment, k) for k in award_fields))
                                    for r in parsed if r.assignment is not None)
            persisted_awards = Counter((order_by_id[a['award_result_id']], tuple(a[k] for k in award_fields))
                                       for a in source_awards)
            if parsed_awards != persisted_awards:
                raise ValueError('pdf_assignment_extraction_mismatch')
        old_by_order = {r['order_number']: r for r in old if target_counts[r['order_number']] == 1}
        fields = copy_fields(source, destination, 'award_results')
        identity_fields = tuple(k for k in fields if k not in ('document_id', 'created_at', 'raw_block_text'))
        if not {'order_number','person_name_normalized','person_display_name','status','specialty_code','list_scope'} <= set(identity_fields):
            raise ValueError('result_identity_fields_missing')
        conflicts = []
        for row in rows:
            prior = old_by_order.get(row['order_number'])
            if prior and (prior['raw_block_text'] != row['raw_block_text'] or
                          any(prior[k] != row[k] for k in identity_fields)):
                benign = (source_counts[row['order_number']] == 1 and row['id'] not in source_assignments
                          and prior['id'] not in target_assignments
                          and all(prior[k] == row[k] for k in identity_fields))
                conflicts.append((row['order_number'], benign))
        unsafe = (None in source_counts or None in target_counts or any(v != 1 for v in source_counts.values())
                  or any(v != 1 for v in target_counts.values()) or any(not benign for _, benign in conflicts))
        destination.execute('''CREATE SCHEMA IF NOT EXISTS recovery_unawarded;
          CREATE TABLE IF NOT EXISTS recovery_unawarded.quarantine(
            document_sha text NOT NULL, ordinal integer NOT NULL, reason text NOT NULL,
            PRIMARY KEY(document_sha,ordinal,reason));
          CREATE TABLE IF NOT EXISTS recovery_unawarded.document_evidence(
            document_sha text PRIMARY KEY, document_id bigint NOT NULL REFERENCES documents(id),
            html_sha256 text NOT NULL, page_url text NOT NULL, document_url text NOT NULL)''')
        if not target_docs:
            proof=(document_sha,did,provenance['html_sha256'],provenance['page_url'],provenance['document_url'])
            destination.execute('INSERT INTO recovery_unawarded.document_evidence VALUES(%s,%s,%s,%s,%s)',proof)
        for ordinal, _ in conflicts:
            destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                (document_sha, ordinal, 'raw_block_conflict_unresolved'))
        counts = Counter()
        result_map = {}
        for row in rows:
            prior = old_by_order.get(row['order_number'])
            if prior:
                if prior['raw_block_text'] == row['raw_block_text'] and all(prior[k] == row[k] for k in identity_fields):
                    counts['reused'] += 1
                    result_map[row['id']] = prior['id']
                else:
                    counts['quarantined'] += 1
                continue
            if (unsafe or row['order_number'] is None or source_counts[row['order_number']] != 1
                    or (not include_awards and (row['id'] in source_assignments or row['status'].strip().casefold() != 'no adjudicat'))):
                counts['not_eligible'] += 1
                continue
            data = {field: row[field] for field in fields}
            data['document_id'] = did
            result_map[row['id']] = insert_row(destination, 'award_results', data)
            counts['inserted'] += 1
        if include_awards:
            source_awards_by_result = Counter(a['award_result_id'] for a in source_awards)
            target_by_result = {}
            for award in target_awards:
                target_by_result.setdefault(award['award_result_id'], []).append(award)
            assignment_fields = copy_fields(source, destination, 'award_assignments')
            source_rows_by_id = {row['id']: row for row in rows}
            for award in source_awards:
                result_id = result_map.get(award['award_result_id'])
                ordinal = source_rows_by_id[award['award_result_id']]['order_number']
                if result_id is None or source_awards_by_result[award['award_result_id']] != 1:
                    counts['awards_quarantined'] += 1
                    if ordinal is not None:
                        destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                            (document_sha, ordinal, 'award_identity_ambiguous'))
                    continue
                match = award.get('matched_offered_position_id')
                verified_offer_id = None
                if match is not None and accredited_offer_sha:
                    offer = source.execute('''SELECT p.*,v.sha256 AS source_sha FROM offered_positions p
                        JOIN documents d ON d.id=p.document_id JOIN document_versions v ON v.id=d.document_version_id
                        WHERE p.id=%s''', (match,)).fetchone()
                    result_row = source_rows_by_id[award['award_result_id']]
                    specialty = award['position_specialty_code'] or result_row['specialty_code']
                    if (offer and offer['source_sha'] == accredited_offer_sha and award['position_code']
                            and award['center_code'] and specialty and
                            (award['position_code'],award['center_code'],specialty) ==
                            (offer['position_code'],offer['center_code'],offer['specialty_code'])):
                        candidates = destination.execute('''SELECT p.id FROM offered_positions p JOIN documents d ON d.id=p.document_id
                            JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s
                            AND p.position_code=%s AND p.center_code=%s AND p.specialty_code=%s
                            AND p.body_code IS NOT DISTINCT FROM %s''',
                            (accredited_offer_sha,offer['position_code'],offer['center_code'],offer['specialty_code'],offer['body_code'])).fetchall()
                        if len(candidates) == 1:
                            verified_offer_id = candidates[0]['id']
                matches = target_by_result.get(result_id, [])
                if matches:
                    if len(matches) == 1 and all(matches[0][k] == award[k] for k in award_fields):
                        counts['awards_reused'] += 1
                        existing_link = matches[0]['matched_offered_position_id']
                        if verified_offer_id is not None and existing_link is None:
                            updated = destination.execute('UPDATE award_assignments SET matched_offered_position_id=%s WHERE id=%s AND matched_offered_position_id IS NULL',
                                                          (verified_offer_id, matches[0]['id']))
                            if updated.rowcount != 1:
                                raise ValueError('association_changed_concurrently')
                            counts['associations_inserted'] += 1
                        elif match is not None and (verified_offer_id is None or existing_link != verified_offer_id):
                            counts['associations_quarantined'] += 1
                            destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                                (document_sha, ordinal, 'offer_identity_unverified'))
                        elif match is None and existing_link is not None:
                            counts['associations_quarantined'] += 1
                            destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                                (document_sha, ordinal, 'target_association_without_source_evidence'))
                    else:
                        counts['awards_quarantined'] += 1
                        destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                            (document_sha, ordinal, 'award_fields_conflict'))
                    continue
                data = {field: award[field] for field in assignment_fields}
                data['award_result_id'] = result_id
                data['matched_offered_position_id'] = verified_offer_id
                if verified_offer_id is not None:
                    counts['associations_inserted'] += 1
                if match is not None and data['matched_offered_position_id'] is None:
                    counts['associations_quarantined'] += 1
                    destination.execute('INSERT INTO recovery_unawarded.quarantine VALUES(%s,%s,%s) ON CONFLICT DO NOTHING',
                                        (document_sha, ordinal, 'offer_identity_unverified'))
                insert_row(destination, 'award_assignments', data)
                counts['awards_inserted'] += 1
        return dict(counts)

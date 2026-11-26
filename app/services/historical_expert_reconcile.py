"""Conservative reconciliation of an attested unawarded-expert procedure.

This adapter handles one document, not ordinary offers or award results.
It is restricted to explicitly fenced isolated trials; it cannot connect to a
database itself. Conflicts abort the transaction instead of overwriting rows.
"""
from collections import Counter
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from app.services.unawarded_expert_vacancies_parser import parse as parse_expert_pdf

from app.services.discovery.verified_evidence import verify_capture
from app.services.historical_batch_reconcile import copy_fields
from scripts.integrate_03nov_trial import insert_row

VACANCY_FIELDS = ('center_code', 'source_position_id', 'province', 'locality',
                  'center_name', 'cycle', 'modules', 'teaching_hours',
                  'total_hours', 'valencian_source', 'result_status')
PROCEDURE_FIELDS = ('procedure_key', 'procedure_kind', 'procedure_date', 'date_evidence')


def _trial_fence(db, synthetic_token):
    info = db.execute("SELECT current_database() AS name,current_setting('data_directory') AS directory,"
                      "current_setting('listen_addresses') AS listen,current_setting('server_version_num') AS version,"
                      "current_setting('funkcionario.validation_token',true) AS token").fetchone()
    if info['listen'] or not 160000 <= int(info['version']) < 170000:
        raise ValueError('destination_not_isolated_postgres16')
    synthetic = bool(synthetic_token and info['name'] == 'funk_validation' and info['token'] == synthetic_token)
    isolated = info['name'] == 'baseline_future_trial' and info['directory'] == '/work/data'
    if not (synthetic or isolated):
        raise ValueError('destination_not_authorized_trial')


def reconcile_expert_procedure(source, destination, *, sha256, html_bytes, html_sha256,
                               page_url, document_url, pdf_bytes, procedure_key,
                               synthetic_token=None):
    """Integrate missing verified rows; retain other destination rows untouched.

    Captures prove byte integrity and a link, not publisher authenticity or
    publication date. The caller must establish the procedure relationship.
    """
    if source.info.transaction_status != TransactionStatus.IDLE or destination.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    if not pdf_bytes.startswith(b'%PDF-'):
        raise ValueError('invalid_pdf')
    verify_capture(html_bytes=html_bytes, expected_html_sha256=html_sha256,
                   final_page_url=page_url, linked_url=document_url,
                   pdf_bytes=pdf_bytes, expected_pdf_sha256=sha256)
    if not document_url.startswith('https://ceice.gva.es/documents/') or not page_url.startswith('https://ceice.gva.es/'):
        raise ValueError('official_evidence_url_required')
    with TemporaryDirectory(prefix='funk-expert-pdf-') as directory:
        path = Path(directory) / 'source.pdf'
        path.write_bytes(pdf_bytes)
        procedure_date, parsed = parse_expert_pdf(path)
    source.row_factory = destination.row_factory = dict_row
    with source.transaction(), destination.transaction():
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        _trial_fence(destination, synthetic_token)
        source_doc = source.execute('''SELECT d.* FROM documents d JOIN document_versions v
            ON v.id=d.document_version_id WHERE v.sha256=%s''', (sha256,)).fetchall()
        if len(source_doc) != 1:
            raise ValueError('source_document_identity_ambiguous')
        doc = source_doc[0]
        version = source.execute('SELECT * FROM document_versions WHERE id=%s', (doc['document_version_id'],)).fetchone()
        proc = source.execute('SELECT * FROM historical_procedures WHERE procedure_key=%s', (procedure_key,)).fetchone()
        link = source.execute('SELECT * FROM historical_procedure_documents WHERE procedure_key=%s AND document_id=%s',
                              (procedure_key, doc['id'])).fetchone()
        if not proc or not link or proc['publication_date'] is not None or proc['publication_date_quality'] != 'unknown':
            raise ValueError('procedure_evidence_missing_or_date_not_unknown')
        if str(proc['procedure_date']) != procedure_date:
            raise ValueError('procedure_date_pdf_mismatch')
        rows = source.execute('SELECT * FROM historical_unawarded_expert_vacancies WHERE document_id=%s', (doc['id'],)).fetchall()
        expected = Counter(tuple(sorted(asdict(item).items())) for item in parsed)
        if not parsed or expected != Counter(tuple(sorted((key,row[key]) for key in asdict(parsed[0]))) for row in rows):
            raise ValueError('source_vacancies_pdf_mismatch')
        parse = source.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' "
                               "ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1", (doc['document_version_id'],)).fetchone()
        keys = [(r['center_code'], r['source_position_id']) for r in rows]
        if (not rows or not parse or parse['rows_extracted'] != len(rows) or len(set(keys)) != len(keys)
                or any(not all(r[x] is not None and r[x] != '' for x in ('center_code', 'source_position_id'))
                       or r['result_status'] != 'unawarded_no_requests' for r in rows)):
            raise ValueError('source_vacancies_not_complete_or_unique')
        target_source = destination.execute("SELECT id,source_url FROM sources WHERE source_key='rrhh_main'").fetchall()
        if len(target_source) != 1 or target_source[0]['source_url'] != 'https://ceice.gva.es/es/web/rrhh-educacion':
            raise ValueError('official_source_identity_conflict')
        target_versions = destination.execute('SELECT id FROM document_versions WHERE sha256=%s', (sha256,)).fetchall()
        if len(target_versions) > 1:
            raise ValueError('target_version_ambiguous')
        result = Counter()
        if not target_versions:
            data = {k: version[k] for k in copy_fields(source, destination, 'document_versions')}
            if 'notification_ingest_intent' in data:
                data['notification_ingest_intent'] = 'unknown'
            vid = insert_row(destination, 'document_versions', data)
            pdata = {k: parse[k] for k in copy_fields(source, destination, 'document_parse_runs')}
            pdata['document_version_id'] = vid
            pid = insert_row(destination, 'document_parse_runs', pdata)
            ddata = {k: doc[k] for k in copy_fields(source, destination, 'documents')}
            ddata.update(document_version_id=vid, source_id=target_source[0]['id'], validation_status='needs_validation',
                         validation_parse_run_id=pid, historical_status='unknown', publication_date_quality='unknown')
            did = insert_row(destination, 'documents', ddata)
            result['documents_inserted'] += 1
        else:
            target = destination.execute('SELECT * FROM documents WHERE document_version_id=%s', (target_versions[0]['id'],)).fetchall()
            if len(target) != 1:
                raise ValueError('target_document_identity_conflict')
            did = target[0]['id']
            result['documents_reused'] += 1
        previous = destination.execute('SELECT * FROM historical_procedures WHERE procedure_key=%s', (procedure_key,)).fetchone()
        if previous:
            if any(previous[k] != proc[k] for k in PROCEDURE_FIELDS):
                raise ValueError('procedure_identity_conflict')
            result['procedures_reused'] += 1
        else:
            destination.execute('''INSERT INTO historical_procedures
                (procedure_key,procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality)
                VALUES(%s,%s,%s,%s,NULL,'unknown')''', tuple(proc[k] for k in PROCEDURE_FIELDS))
            result['procedures_inserted'] += 1
        old_link = destination.execute('SELECT * FROM historical_procedure_documents WHERE procedure_key=%s AND document_id=%s',
                                       (procedure_key, did)).fetchone()
        if old_link:
            if any(old_link[k] != link[k] for k in ('document_role', 'relationship_evidence')):
                raise ValueError('document_procedure_link_conflict')
        else:
            destination.execute('INSERT INTO historical_procedure_documents VALUES(%s,%s,%s,%s)',
                                (procedure_key, did, link['document_role'], link['relationship_evidence']))
        destination.execute('''CREATE SCHEMA IF NOT EXISTS recovery_expert;
            CREATE TABLE IF NOT EXISTS recovery_expert.document_evidence
            (sha256 text PRIMARY KEY, document_id bigint NOT NULL REFERENCES documents(id),
             html_sha256 text NOT NULL, page_url text NOT NULL, document_url text NOT NULL)''')
        evidence=(sha256,did,html_sha256,page_url,document_url)
        destination.execute('INSERT INTO recovery_expert.document_evidence VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',evidence)
        if tuple(destination.execute('SELECT * FROM recovery_expert.document_evidence WHERE sha256=%s',(sha256,)).fetchone().values()) != evidence:
            raise ValueError('expert_document_evidence_conflict')
        for row in rows:
            old = destination.execute('''SELECT * FROM historical_unawarded_expert_vacancies
                WHERE document_id=%s AND center_code=%s AND source_position_id=%s''',
                                      (did, row['center_code'], row['source_position_id'])).fetchone()
            if old:
                if any(old[k] != row[k] for k in VACANCY_FIELDS):
                    raise ValueError('vacancy_fields_conflict')
                result['vacancies_reused'] += 1
            else:
                destination.execute('''INSERT INTO historical_unawarded_expert_vacancies
                    (document_id,center_code,source_position_id,province,locality,center_name,cycle,modules,
                     teaching_hours,total_hours,valencian_source,result_status)
                    VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                    (did, *(row[k] for k in VACANCY_FIELDS)))
                result['vacancies_inserted'] += 1
        return dict(result)

"""Isolated, document-scoped reconciliation of the 19 November listings.

The two award documents are independent transaction units.  The caller must
retain the existing quarantine of the two offer documents; no offer matching
is performed here.
"""
from pathlib import Path
import hashlib
import json
from collections import Counter
from dataclasses import fields
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from app.services.historical_unawarded_reconcile import reconcile_unawarded
from app.services.historical_secondary_reconcile import reconcile_secondary, _trial_fence
from app.services.historical_batch_reconcile import copy_fields
from app.services.offered_positions_parser import OfferedPositionsParserService, ParsedOfferedPosition
from app.services.discovery.verified_evidence import verify_capture
from scripts.integrate_03nov_trial import insert_row


def _safe_error(exc):
    reason=str(exc)
    return reason if isinstance(exc,ValueError) and reason and len(reason)<80 and all(c.isascii() and (c.isalnum() or c=='_') for c in reason) else type(exc).__name__


def quarantine_offers(source, destination, *, document_sha, pdf_path, provenance, stage_label, synthetic_token=None):
    """Compare PDF and source rows, then retain their multiset only in quarantine."""
    if source.info.transaction_status != TransactionStatus.IDLE or destination.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    if stage_label not in ('provisional', 'definitivo'):
        raise ValueError('invalid_stage_label')
    path = Path(pdf_path)
    if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != document_sha:
        raise ValueError('pdf_identity_mismatch')
    verify_capture(html_bytes=provenance['html_bytes'], expected_html_sha256=provenance['html_sha256'],
                   final_page_url=provenance['page_url'], linked_url=provenance['document_url'],
                   pdf_bytes=path.read_bytes(), expected_pdf_sha256=document_sha)
    for key in ('page_url', 'document_url'):
        u = urlsplit(provenance[key])
        if u.scheme != 'https' or u.hostname != 'ceice.gva.es' or u.username or u.password:
            raise ValueError('official_provenance_required')
    if not urlsplit(provenance['document_url']).path.startswith('/documents/'):
        raise ValueError('official_document_path_required')
    # The reconstructed rows were parsed with this source scope. This is a
    # parser parameter, not evidence of publication date or current validity.
    parsed = OfferedPositionsParserService()._parse_document({'file_path':str(path),'list_scope':'inicio_curso'})
    if not parsed: raise ValueError('empty_offer_extraction')
    names = tuple(f.name for f in fields(ParsedOfferedPosition))
    source.row_factory = destination.row_factory = dict_row
    with source.transaction(), destination.transaction():
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        destination.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        _trial_fence(destination, synthetic_token)
        q = '''SELECT d.* FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
        docs = source.execute(q,(document_sha,)).fetchall(); targets = destination.execute(q,(document_sha,)).fetchall()
        if len(docs)!=1 or len(targets)>1: raise ValueError('document_identity_ambiguous')
        doc = docs[0]
        rows = source.execute('SELECT * FROM offered_positions WHERE document_id=%s',(doc['id'],)).fetchall()
        if Counter(tuple(getattr(r,k) for k in names) for r in parsed) != Counter(tuple(r[k] for k in names) for r in rows):
            raise ValueError('pdf_offer_extraction_mismatch')
        if not targets:
            roots=destination.execute("SELECT id,source_url FROM sources WHERE source_key='rrhh_main'").fetchall()
            if len(roots)!=1 or roots[0]['source_url']!='https://ceice.gva.es/es/web/rrhh-educacion': raise ValueError('official_source_missing')
            version=source.execute('SELECT * FROM document_versions WHERE id=%s',(doc['document_version_id'],)).fetchone()
            run=source.execute("SELECT * FROM document_parse_runs WHERE document_version_id=%s AND status='success' ORDER BY finished_at DESC NULLS LAST,id DESC LIMIT 1",(version['id'],)).fetchone()
            if not run or run['rows_extracted']!=len(rows): raise ValueError('source_parse_run_not_complete')
            data={k:version[k] for k in copy_fields(source,destination,'document_versions')}
            if 'notification_ingest_intent' in data: data['notification_ingest_intent']='unknown'
            vid=insert_row(destination,'document_versions',data)
            run_data={k:run[k] for k in copy_fields(source,destination,'document_parse_runs')}; run_data['document_version_id']=vid
            pid=insert_row(destination,'document_parse_runs',run_data)
            data={k:doc[k] for k in copy_fields(source,destination,'documents')}
            data.update(document_version_id=vid,source_id=roots[0]['id'],validation_status='needs_validation',validation_parse_run_id=pid,historical_status='unknown',publication_date_quality='unknown')
            did=insert_row(destination,'documents',data)
        else: did=targets[0]['id']
        destination.execute('''CREATE SCHEMA IF NOT EXISTS recovery_quarantine;
          CREATE TABLE IF NOT EXISTS recovery_quarantine.positions_19nov(source_sha text NOT NULL,source_id bigint NOT NULL,
          document_id bigint NOT NULL REFERENCES documents(id),payload jsonb NOT NULL,stage_label text NOT NULL,PRIMARY KEY(source_sha,source_id));
          CREATE TABLE IF NOT EXISTS recovery_quarantine.evidence_19nov(source_sha text PRIMARY KEY,document_id bigint NOT NULL REFERENCES documents(id),
          html_sha256 text NOT NULL,page_url text NOT NULL,document_url text NOT NULL,stage_label text NOT NULL)''')
        proof=(document_sha,did,provenance['html_sha256'],provenance['page_url'],provenance['document_url'],stage_label)
        destination.execute('INSERT INTO recovery_quarantine.evidence_19nov VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING',proof)
        if tuple(destination.execute('SELECT * FROM recovery_quarantine.evidence_19nov WHERE source_sha=%s',(document_sha,)).fetchone().values())!=proof:
            raise ValueError('offer_evidence_conflict')
        def key(value): return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
        expected=Counter(key({k:r[k] for k in names}) for r in rows)
        existing=destination.execute('SELECT * FROM recovery_quarantine.positions_19nov WHERE source_sha=%s',(document_sha,)).fetchall()
        if any(r['document_id']!=did or r['stage_label']!=stage_label for r in existing): raise ValueError('offer_quarantine_identity_conflict')
        present=Counter(key(r['payload']) for r in existing)
        if present-expected: raise ValueError('offer_quarantine_payload_conflict')
        inserted=0
        for payload,total in expected.items():
            for occurrence in range(present[payload],total):
                ident=int(hashlib.sha256((document_sha+'\0'+payload+'\0'+str(occurrence)).encode()).hexdigest()[:15],16)
                destination.execute('INSERT INTO recovery_quarantine.positions_19nov VALUES(%s,%s,%s,%s,%s)',(document_sha,ident,did,Jsonb(json.loads(payload)),stage_label))
                inserted+=1
        return {'rows':len(rows),'reused':sum(present.values()),'inserted':inserted}


def _link_procedure(source, destination, sha, synthetic_token, key='interino-2026-11-19'):
    """Copy only an explicit source relation, resolving the document by SHA."""
    with source.transaction(), destination.transaction():
        source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        destination.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        _trial_fence(destination, synthetic_token)
        proc = source.execute('SELECT * FROM historical_procedures WHERE procedure_key=%s', (key,)).fetchone()
        if not proc or proc['publication_date'] is not None or proc['publication_date_quality'] != 'unknown':
            raise ValueError('source_procedure_not_sufficiently_accredited')
        links = source.execute('''SELECT l.document_role,l.relationship_evidence FROM historical_procedure_documents l
            JOIN documents d ON d.id=l.document_id JOIN document_versions v ON v.id=d.document_version_id
            WHERE l.procedure_key=%s AND v.sha256=%s''', (key, sha)).fetchall()
        target = destination.execute('''SELECT d.id FROM documents d JOIN document_versions v ON v.id=d.document_version_id
            WHERE v.sha256=%s''', (sha,)).fetchall()
        if len(links) != 1 or len(target) != 1 or not links[0]['relationship_evidence']:
            raise ValueError('procedure_document_identity_ambiguous')
        fields = ('procedure_key','procedure_kind','procedure_date','date_evidence','publication_date','publication_date_quality')
        old = destination.execute('SELECT * FROM historical_procedures WHERE procedure_key=%s', (key,)).fetchone()
        if old:
            if any(old[k] != proc[k] for k in ('procedure_kind','procedure_date','date_evidence')):
                raise ValueError('procedure_identity_conflict')
        else:
            destination.execute('''INSERT INTO historical_procedures
                (procedure_key,procedure_kind,procedure_date,date_evidence,publication_date,publication_date_quality)
                VALUES(%s,%s,%s,%s,%s,%s)''', tuple(proc[k] for k in fields))
        link = links[0]
        existing = destination.execute('SELECT * FROM historical_procedure_documents WHERE procedure_key=%s AND document_id=%s', (key,target[0]['id'])).fetchone()
        if existing and any(existing[k] != link[k] for k in ('document_role','relationship_evidence')):
            raise ValueError('procedure_document_conflict')
        if not existing:
            destination.execute('INSERT INTO historical_procedure_documents VALUES(%s,%s,%s,%s)',
                (key,target[0]['id'],link['document_role'],link['relationship_evidence']))
        return {'status': 'reused' if existing else 'inserted'}


def reconcile_19nov(source, destination, *, maestros, secondary, offers=(), execute=False, synthetic_token=None):
    """Plan by default; execute each checked document in its own transaction.

    Each input supplies document_sha, pdf_path and provenance.  A conflict in
    one listing cannot commit part of that listing or stop the other listing.
    This is restricted to the same isolated trial fence as the underlying
    adapters and deliberately does not promote the quarantined offer rows.
    """
    if source.info.transaction_status != TransactionStatus.IDLE or destination.info.transaction_status != TransactionStatus.IDLE:
        raise ValueError('connections_must_start_idle')
    if maestros['document_sha'] == secondary['document_sha']:
        raise ValueError('distinct_document_identity_required')
    source.row_factory = destination.row_factory = dict_row
    report = {'mode': 'execute' if execute else 'plan', 'documents': {},
              'offer_matches_created': 0, 'procedure_link_status': 'pending'}
    for kind, spec in (('maestros', maestros), ('secondary', secondary)):
        path = Path(spec['pdf_path'])
        if path.is_symlink() or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != spec['document_sha']:
            report['documents'][kind] = {'status': 'failed', 'reason': 'pdf_identity_mismatch'}
            continue
        if not execute:
            with source.transaction(), destination.transaction():
                source.execute('SET TRANSACTION READ ONLY')
                destination.execute('SET TRANSACTION READ ONLY')
                _trial_fence(destination, synthetic_token)
                q = '''SELECT count(*) AS n FROM documents d JOIN document_versions v ON v.id=d.document_version_id WHERE v.sha256=%s'''
                a = source.execute(q, (spec['document_sha'],)).fetchone()['n']
                b = destination.execute(q, (spec['document_sha'],)).fetchone()['n']
                report['documents'][kind] = {'status': 'planned', 'source_document_count': a, 'destination_document_count': b}
            continue
        try:
            args = dict(document_sha=spec['document_sha'], pdf_path=path,
                        provenance=spec.get('provenance'), synthetic_token=synthetic_token)
            if kind == 'maestros':
                result = reconcile_unawarded(source, destination, family='maestros', include_awards=True,
                                             accredited_offer_sha=None, **args)
            else:
                result = reconcile_secondary(source, destination, **args)
            report['documents'][kind] = {'status': 'applied', 'result': result}
            try:
                report['documents'][kind]['procedure_link'] = _link_procedure(source, destination, spec['document_sha'], synthetic_token)
            except (ValueError, psycopg.Error) as exc:
                report['documents'][kind]['procedure_link'] = {'status': 'pending', 'reason': type(exc).__name__}
        except (ValueError, psycopg.Error) as exc:
            # Do not record SQL values, document text or participant identities.
            report['documents'][kind] = {'status': 'failed', 'reason': type(exc).__name__}
    report['offers'] = []
    for spec in offers:
        if not execute:
            report['offers'].append({'status': 'planned', 'sha256': spec['document_sha']})
            continue
        try:
            result = quarantine_offers(source, destination, document_sha=spec['document_sha'],
                pdf_path=spec['pdf_path'], provenance=spec['provenance'], stage_label=spec['stage_label'], synthetic_token=synthetic_token)
            try:
                link = _link_procedure(source, destination, spec['document_sha'], synthetic_token)
            except (ValueError, psycopg.Error) as exc:
                link = {'status':'pending','reason':type(exc).__name__}
            report['offers'].append({'status':'quarantined','sha256':spec['document_sha'],'result':result,'procedure_link':link})
        except (ValueError, psycopg.Error) as exc:
            report['offers'].append({'status':'failed','sha256':spec['document_sha'],'reason':_safe_error(exc)})
    if execute:
        report['procedure_link_status'] = ('partial' if any(x.get('procedure_link',{}).get('status') in ('inserted','reused')
                                                           for x in report['documents'].values()) else 'pending')
        report['status'] = 'partial'  # Complete status requires external procedure-wide verification.
    return report

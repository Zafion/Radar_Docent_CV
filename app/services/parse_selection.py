"""Conservative selection shared by every parser; no persistent schema changes."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re


@dataclass(frozen=True)
class Selection:
    entries: tuple = ()
    reprocess: bool = False
    selective_live: bool = False


CURRENT_SELECTION = ContextVar('parse_selection', default=Selection())


@contextmanager
def selection_scope(selection):
    token = CURRENT_SELECTION.set(selection)
    try:
        yield
    finally:
        CURRENT_SELECTION.reset(token)


def metadata(conn, ids):
    if not ids:
        return {}
    rows = conn.execute('''SELECT d.id AS document_id,d.document_version_id,d.doc_family,d.list_scope,
        d.parsed_at,v.sha256,v.file_path,v.notification_ingest_intent AS ingest_intent,
        ns.intent AS detection_intent,ns.corrected_url,ns.delivery_hold_reason,
        (SELECT max(p.id) FROM document_parse_runs p
         WHERE p.document_version_id=d.document_version_id) AS latest_run_id
        FROM documents d JOIN document_versions v ON v.id=d.document_version_id
        LEFT JOIN notification_document_state ns ON ns.document_id=d.id
        WHERE d.id=ANY(%s)''', (list(ids),)).fetchall()
    return {row['document_id']: dict(row) for row in rows}


def reason(item, parser_key, parser_version, selection=None):
    selection = selection or CURRENT_SELECTION.get()
    if item['doc_family'] == 'unknown':
        return 'unknown_requires_separate_classification'
    if selection.selective_live:
        match = next((e for e in selection.entries
                      if e['document_id'] == item['document_id']
                      and e['document_version_id'] == item['document_version_id']
                      and e['sha256'] == item['sha256']), None)
        if match is None:
            return 'outside_explicit_live_selection'
    if selection.reprocess:
        match = next((e for e in selection.entries
                      if e['document_id'] == item['document_id']
                      and e['document_version_id'] == item['document_version_id']
                      and e['sha256'] == item['sha256']
                      and e['parser_key'] == parser_key and e['parser_version'] == parser_version
                      and e['doc_family'] == item['doc_family']), None)
        if match is None:
            return 'outside_explicit_plan'
        if match['expected_latest_run_id'] != item['latest_run_id']:
            return 'plan_consumed_or_stale'
        return None
    if os.getenv('NOTIFICATION_PROCESSING_INTENT', 'normal') != 'normal':
        return 'explicit_reprocess_plan_required'
    if item['latest_run_id'] is not None or item['parsed_at'] is not None:
        return 'previous_attempt_requires_plan'
    if item['ingest_intent'] != 'live_new' or item['detection_intent'] != 'live_new':
        return 'non_live_requires_plan'
    if item['corrected_url'] or item['delivery_hold_reason']:
        return 'correction_or_hold_requires_plan'
    return None


def selective_live_selection(entries):
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError('explicit_nonempty_bounded_selection_required')
    seen = set()
    verified = []
    for entry in entries:
        if (not isinstance(entry, dict)
                or set(entry) != {'document_id', 'document_version_id', 'sha256'}
                or any(type(entry[k]) is not int or entry[k] < 1
                       for k in ('document_id', 'document_version_id'))
                or not isinstance(entry['sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
            raise ValueError('invalid_live_selection_entry')
        if entry['document_version_id'] in seen:
            raise ValueError('duplicate_document_in_selection')
        seen.add(entry['document_version_id'])
        verified.append(dict(entry))
    return Selection(tuple(verified), False, True)


def filter_candidates(conn, rows, parser_key, parser_version):
    state = metadata(conn, [r['document_id'] for r in rows])
    return [dict(r) for r in rows if r['document_id'] in state
            and reason(state[r['document_id']], parser_key, parser_version) is None]


def verified_sha(path, expected):
    if not re.fullmatch(r'[0-9a-f]{64}', expected):
        raise ValueError('invalid_sha256')
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise ValueError('document_bytes_do_not_match_sha256')


def locked_documents(store, rows, parser_key, parser_version):
    """Session locks survive parser commits/rollbacks. Recheck after acquiring.

    Negative version IDs reserve one advisory namespace shared by all families.
    A closed connection also releases the lock after a process interruption.
    """
    conn = store.connection
    for row in rows:
        version = int(row['document_version_id'])
        conn.rollback()  # End selection snapshots before claiming the document.
        if not conn.execute('SELECT pg_try_advisory_lock(%s::bigint)', (-version,)).fetchone()[0]:
            continue
        try:
            item = metadata(conn, [row['document_id']]).get(row['document_id'])
            if item is None or item['document_version_id'] != version:
                continue
            if reason(item, parser_key, parser_version):
                continue
            verified_sha(item['file_path'], item['sha256'])
            yield row
        finally:
            # Parsers commit successful/failed attempts themselves. On abrupt
            # exits only uncommitted work is rolled back before releasing.
            if not conn._conn.closed:
                conn.rollback()
                conn.execute('SELECT pg_advisory_unlock(%s::bigint)', (-version,))
                conn.commit()


def selection_from_plan(plan):
    if plan.get('format') != 'funkcionario.parse-plan.v1' or plan.get('mode') != 'reprocess':
        raise ValueError('invalid_reprocess_plan')
    entries = plan.get('entries')
    if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
        raise ValueError('explicit_nonempty_bounded_plan_required')
    seen = set()
    for e in entries:
        if (not isinstance(e, dict) or not re.fullmatch(r'[0-9a-f]{64}', e.get('sha256', ''))
                or any(type(e.get(k)) is not int or e[k] < 1 for k in ('document_id', 'document_version_id'))
                or 'expected_latest_run_id' not in e
                or (e.get('expected_latest_run_id') is not None
                    and (type(e['expected_latest_run_id']) is not int or e['expected_latest_run_id'] < 1))
                or not all(isinstance(e.get(k), str) and e[k] for k in ('parser_key', 'parser_version', 'doc_family'))):
            raise ValueError('invalid_plan_entry')
        key = e['document_version_id']
        if key in seen:
            raise ValueError('duplicate_document_in_plan')
        seen.add(key)
    return Selection(tuple(entries), True)

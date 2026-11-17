"""Read-only planning and explicit reviewed-plan execution. No reclassification."""
import json
from pathlib import Path
import re

from app.services.parse_selection import (Selection, metadata, reason, verified_sha,
                                          selection_from_plan, selection_scope)


def parsers():
    from app.services import final_award_listing_maestros_parser as m
    from app.services import final_award_listing_secundaria_parser as s
    from app.services import offered_positions_parser as p
    from app.services import difficult_coverage_provisional_parser as d
    from app.services import docent_bag_participants_parser as b
    from app.services import non_docent_parser as n
    from app.storage.docent_bag_store import DOCENT_BAG_DOC_FAMILIES
    return [(m, ('final_award_listing',), 'maestros'),
            (s, ('final_award_listing',), 'secundaria_otros'),
            (p, ('offered_positions',), None),
            (d, ('difficult_coverage_provisional',), None),
            (b, DOCENT_BAG_DOC_FAMILIES, None),
            (n, n.NON_DOCENT_DOC_FAMILIES, None)]


def preview(*, shas=None, parser_key=None, family=None, reprocess=False):
    if reprocess and not shas:
        raise ValueError('explicit_sha_list_required')
    if shas is not None and (not 1 <= len(shas) <= 1000 or
                            any(not re.fullmatch(r'[0-9a-f]{64}', s) for s in shas)):
        raise ValueError('invalid_sha_list')
    definitions = parsers()
    if parser_key and parser_key not in {m.PARSER_KEY for m, _, _ in definitions}:
        raise ValueError('unknown_parser')
    if family and family not in {'unknown'} | {f for _, families, _ in definitions for f in families}:
        raise ValueError('unknown_family')
    from app.storage.db import get_connection
    conn = get_connection()
    try:
        # First statement, before any business read. No stores, registry,
        # parser, reconciler, matcher or advisory locks are called here.
        conn.execute('SET TRANSACTION READ ONLY')
        ids = [r[0] for r in conn.execute('SELECT id FROM documents ORDER BY id').fetchall()]
        state = metadata(conn, ids)
        entries, held, found = [], [], set()
        for item in state.values():
            if shas is not None and item['sha256'] not in shas:
                continue
            if family and item['doc_family'] != family:
                continue
            if item['doc_family'] == 'unknown':
                held.append(dict(document_id=item['document_id'],sha256=item['sha256'],
                                 reason='unknown_requires_separate_classification'))
            for module, families, scope in definitions:
                if parser_key and module.PARSER_KEY != parser_key:
                    continue
                if item['doc_family'] not in families or (scope and item['list_scope'] != scope):
                    continue
                found.add(item['sha256'])
                entry = dict(document_id=item['document_id'], document_version_id=item['document_version_id'],
                             sha256=item['sha256'], doc_family=item['doc_family'],
                             parser_key=module.PARSER_KEY, parser_version=module.PARSER_VERSION,
                             expected_latest_run_id=item['latest_run_id'])
                why = None if reprocess else reason(item, module.PARSER_KEY, module.PARSER_VERSION, Selection())
                if why is None:
                    verified_sha(item['file_path'], item['sha256'])
                    entries.append(entry)
                else:
                    held.append({**entry, 'reason': why})
        if shas is not None and set(shas) != found:
            # Unknown, missing, or mismatched family/scope cannot be silently
            # included in an executable plan. Never classify them here.
            raise ValueError('sha_not_found_in_requested_supported_scope')
        return dict(format='funkcionario.parse-plan.v1', mode='reprocess' if reprocess else 'automatic',
                    entries=entries, held=held, read_only=True)
    finally:
        conn.rollback()
        conn.close()


def execute_plan(plan, *, authorized=False):
    if not authorized:
        raise ValueError('explicit_reprocess_authorization_required')
    selection = selection_from_plan(plan)
    definitions = {m.PARSER_KEY: (m.PARSER_VERSION, fs, scope) for m, fs, scope in parsers()}
    for entry in selection.entries:
        expected = definitions.get(entry['parser_key'])
        if expected is None or expected[0] != entry['parser_version'] or entry['doc_family'] not in expected[1]:
            raise ValueError('plan_parser_changed_or_unsupported')
    # Validate identity/scope before any parser can write. A consumed plan is
    # allowed to become a no-op, but altered or reclassified identities are not.
    from app.storage.db import get_connection
    conn = get_connection()
    try:
        conn.execute('SET TRANSACTION READ ONLY')
        state = metadata(conn, [e['document_id'] for e in selection.entries])
        for entry in selection.entries:
            item = state.get(entry['document_id'])
            scope = definitions[entry['parser_key']][2]
            if (item is None or any(item[k] != entry[k] for k in ('document_version_id','sha256','doc_family'))
                    or (scope is not None and item['list_scope'] != scope)):
                raise ValueError('plan_document_changed_or_wrong_scope')
    finally:
        conn.rollback()
        conn.close()
    from app.services.document_parse_dispatcher import DocumentParseDispatcherService
    with selection_scope(selection):
        return DocumentParseDispatcherService().run()

"""Manifest-v3 selective cycle; execution is currently synthetic-only.

There is intentionally no production authorization implementation here.
"""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.services.document_registry import DocumentRegistryService
from app.services.selective_live_parser import parse_selected
from app.services.selective_lock_supervisor import InheritedLockFence
from app.services.selective_manifest import validate_technical_manifest
from app.services.selective_db_fence import TransactionAuthorization
from app.services.selective_baseline import classify_and_record
from app.storage.db import _require_db_url, selective_transaction_fence
from scripts.import_remote_fetch import import_manifest


def run_synthetic_v3(*, root, cluster_token, staging, manifest_bytes, manifest_sha256, lock_paths, authorization=None, baseline_id=None):
    """Execute only on an owned temporary test cluster, never production."""
    root=Path(root).resolve(); staging=Path(staging).resolve()
    test_root=(Path(__file__).resolve().parents[2]/'auditoria_local').resolve()
    if (not root.is_relative_to(test_root) or not root.name.startswith('pgv_')
            or not staging.is_relative_to(root) or not isinstance(cluster_token,str)
            or len(cluster_token)!=32 or any(c not in '0123456789abcdef' for c in cluster_token)):
        raise ValueError('synthetic_scope_required')
    if len(lock_paths)!=2 or any(not Path(path).resolve().is_relative_to(root) for path in lock_paths):
        raise ValueError('synthetic_locks_required')
    if not isinstance(authorization,TransactionAuthorization) or authorization.scope!='synthetic-cycle':
        raise ValueError('synthetic_transaction_authorization_required')
    if not isinstance(baseline_id,str) or not baseline_id.startswith('synthetic-'):
        raise ValueError('synthetic_baseline_required')
    fence=InheritedLockFence(lock_paths)
    fence.check()
    data=json.loads(manifest_bytes)
    if data.get('format')!='funkcionario.selective-technical.v3':
        raise ValueError('v3_manifest_required')
    entries=validate_technical_manifest(staging,manifest_bytes,manifest_sha256)
    for item in entries:
        asset=item['asset']
        if not asset['source_key'].startswith('synthetic_') or item['identity']!=asset['canonical_url']:
            raise ValueError('synthetic_identity_required')
        for value in (asset['source_url'],asset['url'],asset['canonical_url'],
                      item['evidence']['page_url'],item['evidence']['final_page_url']):
            url=urlsplit(value)
            if url.scheme!='https' or url.hostname not in {'ceice.gva.es','example.invalid'} or url.username or url.password or url.port:
                raise ValueError('synthetic_source_required')
    params=conninfo_to_dict(_require_db_url())
    if (params.get('host')!=str(root/'s') or params.get('port')!='55439'
            or params.get('dbname')!='funk_validation' or params.get('user')!='funk_validation_owner'
            or params.get('service')):
        raise ValueError('synthetic_database_required')
    with psycopg.connect(_require_db_url(),row_factory=dict_row) as conn:
        meta=conn.execute("SELECT current_setting('data_directory') AS d, current_setting('funkcionario.validation_token') AS t, current_setting('listen_addresses') AS l").fetchone()
        if Path(meta['d']).resolve()!=(root/'d').resolve() or meta['t']!=cluster_token or meta['l']:
            raise ValueError('synthetic_database_identity')
        # Hold a shared row lock for the entire cycle; per-write transactions
        # take compatible shared locks. Revocation takes FOR UPDATE and cannot
        # commit between the cycle's individual transactions.
        fence.check()
        authorization.acquire(conn)
        with selective_transaction_fence(fence,authorization):
            from app.storage.db import get_connection
            decisions=[];connection=get_connection()
            try:
                for entry in entries:
                    decisions.append(classify_and_record(connection,baseline_id,entry,staging))
                connection.commit()
            except BaseException:
                connection.rollback();raise
            finally:
                connection.close()
            accepted=[entry for entry,decision in zip(entries,decisions) if decision=='live_new']
            result=_run_validated(accepted,staging,root,fence) if accepted else {'processed':0,'failed':0}
            result['classifications']={kind:decisions.count(kind) for kind in sorted(set(decisions))}
            return result


def _run_validated(entries,staging,root,fence):
    from app.storage.db import get_connection
    db=get_connection()
    try:
        chosen=[]
        for entry in entries:
            fence.check()
            asset=entry['asset']; canonical=asset['canonical_url']; sha=entry['sha256']
            matches=db.execute('''SELECT a.id AS asset_id,a.document_version_id,v.sha256,
                v.notification_ingest_intent,s.source_key,s.source_url,a.canonical_url,
                a.url,a.asset_role,a.title,a.publication_date_text
                FROM assets a JOIN sources s ON s.id=a.source_id
                JOIN document_versions v ON v.id=a.document_version_id
                WHERE a.canonical_url=%s AND v.sha256=%s''',(canonical,sha)).fetchall()
            if len(matches)>1: raise ValueError('ambiguous_identity')
            if matches:
                row=dict(matches[0])
                if row.pop('notification_ingest_intent')!='live_new' or any(
                        row[k]!=asset[k] for k in ('source_key','source_url','canonical_url','url',
                                                   'asset_role','title','publication_date_text')):
                    raise ValueError('historical_or_inconsistent_version')
                for key in ('source_url','url','asset_role','title','publication_date_text'):row.pop(key)
                chosen.append(row)
                continue
            if db.execute('SELECT 1 FROM document_versions WHERE sha256=%s',(sha,)).fetchone():
                raise ValueError('sha_already_associated')
            db.rollback()
            source_asset=dict(asset,downloadable=True)
            result=dict(status='downloaded',asset=source_asset,sha256=sha,
                        original_filename=entry['file'],stored_filename=sha+'.pdf',
                        relative_file_path=entry['file'],size_bytes=entry['size_bytes'],content_type='application/pdf')
            legacy={'schema_version':1,'sources':[{'source_key':asset['source_key'],
                    'source_url':asset['source_url'],'source_label':'synthetic fixture',
                    'status':'success','results':[result]}]}
            fence.check()
            with tempfile.TemporaryDirectory(prefix='selected-',dir=root) as temp:
                incoming=Path(temp)
                fence.check(); (incoming/entry['file']).write_bytes((staging/entry['file']).read_bytes())
                fence.check(); manifest=incoming/'manifest.json'; manifest.write_text(json.dumps(legacy))
                with redirect_stdout(io.StringIO()):
                    import_manifest(manifest_path=manifest,incoming_dir=incoming,
                                    data_dir=root/'imported',notification_intent='live_new')
            db.rollback()
            row=db.execute('''SELECT a.id AS asset_id,a.document_version_id,v.sha256,s.source_key,a.canonical_url
                FROM assets a JOIN sources s ON s.id=a.source_id JOIN document_versions v ON v.id=a.document_version_id
                WHERE a.canonical_url=%s AND v.sha256=%s''',(canonical,sha)).fetchone()
            if row is None: raise ValueError('import_not_confirmed')
            chosen.append(dict(row))
        db.rollback()
    finally:
        db.close()
    registered=DocumentRegistryService().register_selected_live(chosen)
    result=parse_selected(registered)
    fence.check()
    return result

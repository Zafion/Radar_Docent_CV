"""Synthetic-only integration adapter. Never a production authorization path."""
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from psycopg.rows import dict_row

from app.services.selective_manifest import validate_technical_manifest
from app.services.document_registry import DocumentRegistryService
from app.services.selective_live_parser import parse_selected
from scripts.import_remote_fetch import import_manifest


def run_guarded_fixture(cluster, staging, manifest_bytes, expected_sha, lock_paths):
    """Synthetic-only flow with inherited locks checked at DB boundaries."""
    from app.services.selective_lock_supervisor import InheritedLockFence
    from app.storage.db import selective_transaction_fence
    fence=InheritedLockFence(lock_paths)
    with selective_transaction_fence(fence):
        result=run_fixture(cluster,staging,manifest_bytes,expected_sha)
        fence.check()
        return result


def run_fixture(cluster, staging, manifest_bytes, expected_sha):
    """Use only a verified, owned FreshCluster and explicitly synthetic URLs."""
    staging = Path(staging).resolve()
    if not staging.is_relative_to(cluster.root.resolve()):
        raise ValueError('staging_outside_synthetic_cluster')
    entries = validate_technical_manifest(staging, manifest_bytes, expected_sha)
    for e in entries:
        a = e.get('asset')
        if not a or not a['source_key'].startswith('synthetic_'):
            raise ValueError('synthetic_metadata_required')
        for key in ('source_url', 'url', 'canonical_url'):
            u = urlsplit(a[key])
            if u.scheme != 'https' or u.hostname != 'example.invalid' or u.username or u.password or u.port:
                raise ValueError('synthetic_source_required')
        if e['identity'] != a['canonical_url']:
            raise ValueError('identity_mismatch')
    db = cluster.verify(psycopg.connect(cluster.dsn(), row_factory=dict_row))
    try:
        chosen = []
        for e in entries:
            a = e['asset']
            rows = db.execute('''SELECT a.id AS asset_id,a.document_version_id,v.sha256,
                v.notification_ingest_intent,s.source_key,s.source_url,a.canonical_url,
                a.url,a.asset_role,a.title,a.publication_date_text
                FROM assets a JOIN sources s ON s.id=a.source_id
                JOIN document_versions v ON v.id=a.document_version_id
                WHERE a.canonical_url=%s AND v.sha256=%s''', (a['canonical_url'],e['sha256'])).fetchall()
            if len(rows) > 1:
                raise ValueError('ambiguous_identity')
            if rows:
                row = dict(rows[0])
                if row.pop('notification_ingest_intent') != 'live_new' or any(
                        row[key] != a[key] for key in ('source_key','source_url','canonical_url',
                                                        'url','asset_role','title','publication_date_text')):
                    raise ValueError('historical_or_inconsistent_version')
                for key in ('source_url','url','asset_role','title','publication_date_text'):
                    row.pop(key)
                chosen.append(row)
                continue
            if db.execute('SELECT 1 FROM document_versions WHERE sha256=%s',(e['sha256'],)).fetchone():
                raise ValueError('sha_already_associated')
            asset = dict(a, downloadable=True)
            result = dict(status='downloaded', asset=asset, sha256=e['sha256'],
                          original_filename=e['file'], stored_filename=e['sha256']+'.pdf',
                          relative_file_path=e['file'], size_bytes=e['size_bytes'], content_type='application/pdf')
            legacy = {'schema_version':1,'sources':[{'source_key':a['source_key'], 'source_url':a['source_url'],
                      'source_label':'synthetic fixture', 'status':'success', 'results':[result]}]}
            # This is an owned test directory, separate from the exact staging set.
            incoming = cluster.root / ('incoming-' + e['sha256'])
            incoming.mkdir(exist_ok=True)
            target = incoming / e['file']
            if not target.exists():
                target.write_bytes((staging/e['file']).read_bytes())
            manifest = incoming/'manifest.json'
            manifest.write_text(json.dumps(legacy))
            with redirect_stdout(io.StringIO()):
                import_manifest(manifest_path=manifest, incoming_dir=incoming,
                                data_dir=cluster.root/'imported', notification_intent='live_new')
            db.rollback()
            row = db.execute('''SELECT a.id AS asset_id,a.document_version_id,v.sha256,s.source_key,a.canonical_url
                FROM assets a JOIN sources s ON s.id=a.source_id JOIN document_versions v ON v.id=a.document_version_id
                WHERE a.canonical_url=%s AND v.sha256=%s''',(a['canonical_url'],e['sha256'])).fetchone()
            if not row:
                raise ValueError('import_not_confirmed')
            chosen.append(dict(row))
        db.rollback()
    finally:
        db.close()
    registered = DocumentRegistryService().register_selected_live(chosen)
    return parse_selected(registered)

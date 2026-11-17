"""Read-only technical validation. Does not grant provenance or ingestion authority."""
import hashlib
import json
import os
import re
import stat

from app.services.remote_import_integrity import directory, regular, check_bytes


class ManifestRejected(ValueError):
    pass


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ManifestRejected('duplicate_key')
        result[key] = value
    return result


def validate_technical_manifest(staging, manifest_bytes, expected_digest):
    """Check an exact flat set of files, without writing or asserting novelty.

    The expected digest must come from an independent approval channel. The
    returned metadata is not evidence of official provenance or live_new.
    """
    if not isinstance(manifest_bytes, bytes) or len(manifest_bytes) > 1024 * 1024:
        raise ManifestRejected('manifest_size')
    if not isinstance(expected_digest, str) or not re.fullmatch(r'[0-9a-f]{64}', expected_digest):
        raise ManifestRejected('invalid_expected_digest')
    if hashlib.sha256(manifest_bytes).hexdigest() != expected_digest:
        raise ManifestRejected('manifest_digest')
    try:
        data = json.loads(manifest_bytes, object_pairs_hook=_object)
        if not isinstance(data, dict) or set(data) != {'format', 'entries'} or data['format'] not in {'funkcionario.selective-technical.v1', 'funkcionario.selective-technical.v2'}:
            raise ManifestRejected('manifest_schema')
        entries = data['entries']
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ManifestRejected('entry_count')
        names, ids, digests = set(), set(), set()
        for entry in entries:
            expected = {'identity', 'file', 'sha256', 'size_bytes'}
            if data['format'].endswith('.v2'):
                expected.add('asset')
            if not isinstance(entry, dict) or set(entry) != expected:
                raise ManifestRejected('entry_schema')
            if 'asset' in entry:
                asset = entry['asset']
                fields = {'source_key', 'source_url', 'canonical_url', 'url', 'asset_role', 'title', 'publication_date_text'}
                if not isinstance(asset, dict) or set(asset) != fields or any(not isinstance(asset[k], str) or not asset[k] for k in fields):
                    raise ManifestRejected('asset_metadata')
            identity, name, sha, size = (entry[k] for k in ('identity', 'file', 'sha256', 'size_bytes'))
            if not isinstance(identity, str) or not identity or len(identity) > 512:
                raise ManifestRejected('identity')
            if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', name):
                raise ManifestRejected('path')
            if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
                raise ManifestRejected('sha256')
            if type(size) is not int or not 0 < size <= 1024 * 1024 * 1024:
                raise ManifestRejected('size')
            if name in names or identity in ids or sha in digests:
                raise ManifestRejected('duplicate_entry')
            names.add(name); ids.add(identity); digests.add(sha)
        with directory(staging) as root:
            if set(os.listdir(root)) != names:
                raise ManifestRejected('unexpected_or_missing_file')
            for entry in entries:
                with regular(root, entry['file']) as handle:
                    if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                        raise ManifestRejected('not_regular')
                    check_bytes(handle, entry['sha256'], entry['size_bytes'])
        return tuple(dict(entry) for entry in entries)
    except ManifestRejected:
        raise
    except Exception:
        raise ManifestRejected('technical_validation_failed') from None

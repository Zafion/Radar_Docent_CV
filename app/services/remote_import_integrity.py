"""Local remote-import integrity boundary (Linux dirfd / O_NOFOLLOW).

The manifest is an assertion, never a trusted digest of the incoming bytes.
Snapshots are private open, unlinked files; published files are never overwritten.
"""
from contextlib import contextmanager, ExitStack
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import stat
import uuid

LOG = logging.getLogger(__name__)
CHUNK = 1024 * 1024


class ImportIntegrityError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        # Reason codes only: never source URLs, file contents, credentials or names.
        LOG.warning('remote_import_rejected reason=%s rejected_batches=1', reason)
        super().__init__(reason)


def reject(reason):
    raise ImportIntegrityError(reason)


def parts(value):
    if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
        reject('unsafe_path')
    path = Path(value)
    if path.is_absolute() or any(p in {'', '.', '..'} for p in value.split('/')):
        reject('unsafe_path')
    return value.split('/')


def component(value):
    if len(parts(value)) != 1:
        reject('unsafe_component')
    return value


@contextmanager
def directory(path, *, create=False):
    """Pin every directory inode, rejecting symlinks in all path components."""
    absolute = Path(os.path.abspath(path))
    fd = os.open('/', os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            if create:
                try: os.mkdir(part, dir_fd=fd)
                except FileExistsError: pass
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        yield fd
    finally:
        os.close(fd)


@contextmanager
def regular(root, relative):
    names = parts(relative)
    fd = os.dup(root)
    try:
        for name in names[:-1]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        file_fd = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        with os.fdopen(file_fd, 'rb') as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                reject('not_regular_file')
            yield handle
    finally:
        os.close(fd)


@contextmanager
def private_snapshot(root):
    name = '.import-' + uuid.uuid4().hex
    fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
    try:
        os.unlink(name, dir_fd=root)
    except BaseException:
        os.close(fd)
        raise
    with os.fdopen(fd, 'w+b') as handle:
        yield handle


def identity(handle):
    info = os.fstat(handle.fileno())
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def check_bytes(handle, expected_sha, expected_size, destination=None):
    before = identity(handle)
    if before[2] != expected_size:
        reject('size_mismatch')
    digest = hashlib.sha256(); count = 0
    handle.seek(0)
    for chunk in iter(lambda: handle.read(CHUNK), b''):
        count += len(chunk)
        if count > expected_size:
            reject('size_mismatch')
        digest.update(chunk)
        if destination is not None:
            destination.write(chunk)
    if count != expected_size:
        reject('size_mismatch')
    if digest.hexdigest() != expected_sha:
        reject('sha256_mismatch')
    if before != identity(handle):
        reject('source_changed_during_read')
    if destination is not None:
        destination.flush()
        destination.seek(0)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            reject('duplicate_json_key')
        result[key] = value
    return result


@contextmanager
def validated_manifest(manifest_path, incoming_dir, data_dir):
    """Validate EVERY received downloadable, including known and duplicate SHAs.

    Yields snapshots keyed by result object identity. No DB connection is opened
    and no document is published until the entire manifest passes preflight.
    """
    try:
        with ExitStack() as stack:
            incoming = stack.enter_context(directory(incoming_dir))
            relative = Path(os.path.abspath(manifest_path)).relative_to(Path(os.path.abspath(incoming_dir)))
            with regular(incoming, str(relative)) as handle:
                manifest = json.load(handle, object_pairs_hook=_unique_object)
            if type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1:
                reject('unsupported_schema_version')
            data = stack.enter_context(directory(data_dir, create=True))
            snapshots = {}; canonicals = {}; targets = {}; sources = {}
            for source in manifest.get('sources', []):
                key = component(source['source_key'])
                if key in sources and sources[key] != source['source_url']:
                    reject('conflicting_source')
                sources[key] = source['source_url']
                for result in source.get('results', []):
                    asset = result['asset']
                    if asset['source_key'] != key or asset['source_url'] != source['source_url']:
                        reject('asset_source_mismatch')
                    if result.get('status') in {'non_downloadable','download_error','download_error_cached'} or not asset['downloadable']:
                        continue
                    sha = result.get('sha256'); size = result.get('size_bytes')
                    if not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha):
                        reject('invalid_sha256')
                    if type(size) is not int or size < 0:
                        reject('invalid_size_bytes')
                    filename = component(result['stored_filename'])
                    component(result['original_filename'])
                    canonical = asset['canonical_url']
                    if not isinstance(canonical, str) or not canonical:
                        reject('invalid_canonical_url')
                    for registry, index in ((canonicals, canonical), (targets, (key, filename))):
                        if index in registry and registry[index] != (sha, size):
                            reject('conflicting_manifest_identity')
                        registry[index] = (sha, size)
                    snapshot = stack.enter_context(private_snapshot(data))
                    with regular(incoming, result['relative_file_path']) as handle:
                        check_bytes(handle, sha, size, snapshot)
                    snapshots[id(result)] = snapshot
            yield manifest, snapshots
    except ImportIntegrityError:
        raise
    except (OSError, KeyError, TypeError, ValueError) as exc:
        # Do not include exception text: it can contain attacker-provided paths.
        raise ImportIntegrityError('invalid_manifest_or_unavailable_file') from None


def publish_snapshot(snapshot, *, data_dir, source_key, filename, sha256, size_bytes):
    """Publish the exact held snapshot atomically, without overwriting any file."""
    target_dir = Path(data_dir) / component(source_key) / 'files'
    filename = component(filename)
    with directory(target_dir, create=True) as root:
        try:
            with regular(root, filename) as existing:
                check_bytes(existing, sha256, size_bytes)
            return target_dir / filename
        except FileNotFoundError:
            pass
        temporary = '.import-' + uuid.uuid4().hex
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
        try:
            with os.fdopen(fd, 'wb') as output:
                check_bytes(snapshot, sha256, size_bytes, output)
                os.fchmod(output.fileno(), 0o644)
                os.fsync(output.fileno())
            try:
                os.link(temporary, filename, src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
            except FileExistsError:
                with regular(root, filename) as existing:
                    check_bytes(existing, sha256, size_bytes)
            os.fsync(root)
        finally:
            os.unlink(temporary, dir_fd=root)
    return target_dir / filename


def verify_existing_version(existing, *, data_dir, sha256, size_bytes):
    if existing['sha256'] != sha256 or existing['size_bytes'] != size_bytes:
        reject('existing_version_metadata_mismatch')
    try:
        relative = Path(os.path.abspath(existing['file_path'])).relative_to(Path(os.path.abspath(data_dir)))
        with directory(data_dir) as root, regular(root, str(relative)) as handle:
            check_bytes(handle, sha256, size_bytes)
    except ImportIntegrityError:
        raise
    except (OSError, ValueError):
        reject('existing_version_unavailable_or_unsafe')

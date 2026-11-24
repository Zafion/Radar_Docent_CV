"""Bounded document transfer and content validation shared by both fetchers."""
from dataclasses import dataclass
from email.message import Message
from io import BytesIO
from pathlib import Path
import hashlib
import os
import tempfile
from urllib.parse import unquote, urlparse
from zipfile import ZipFile, BadZipFile

from pypdf import PdfReader

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
KNOWN_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.zip', '.doc', '.xls'}


@dataclass(frozen=True)
class DocumentPayload:
    content: bytes
    original_filename: str
    content_type: str
    extension: str


def filename_from_url(url: str) -> str:
    parts = [unquote(part) for part in urlparse(url).path.split('/') if part]
    for part in reversed(parts):
        if Path(part).suffix.lower() in KNOWN_EXTENSIONS:
            return Path(part.replace('\\', '/')).name
    return Path((parts[-1] if parts else 'documento').replace('\\', '/')).name


def read_document_bytes(response, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    if max_bytes <= 0:
        raise ValueError('invalid_download_limit')
    declared = response.headers.get('content-length')
    if declared is not None:
        try:
            declared = int(declared)
        except ValueError as exc:
            raise ValueError('invalid_content_length') from exc
        if declared < 0 or declared > max_bytes:
            raise ValueError('download_size_limit_exceeded')
    parts = []
    size = 0
    for chunk in response.iter_bytes(chunk_size=64 * 1024):
        size += len(chunk)
        if size > max_bytes:
            raise ValueError('download_size_limit_exceeded')
        parts.append(chunk)
    if declared is not None and not response.headers.get('content-encoding') and size != declared:
        raise ValueError('incomplete_document_transfer')
    return b''.join(parts)


def validate_document(content: bytes, url: str, headers=None) -> DocumentPayload:
    headers = headers or {}
    name = filename_from_url(url)
    if disposition := headers.get('content-disposition'):
        message = Message(); message['content-disposition'] = disposition
        proposed = message.get_filename()
        if proposed:
            name = Path(proposed.replace('\\', '/')).name
    name = ''.join(char for char in name if ord(char) >= 32).strip() or 'documento'
    if not content:
        raise ValueError('empty_document')
    if content.lstrip(b'\xef\xbb\xbf \t\r\n').startswith(b'%PDF-'):
        try:
            if b'%%EOF' not in content[-4096:]:
                raise ValueError('missing_pdf_end_marker')
            reader = PdfReader(BytesIO(content))
            # Public PDFs can carry owner/printing permissions yet open
            # without a reading password. Require readable pages, not a false
            # "not encrypted" assumption. Never attempt a nonempty password.
            if len(reader.pages) == 0:
                raise ValueError('unreadable_pdf')
        except Exception as exc:
            raise ValueError('invalid_pdf_document') from exc
        extension, mime = '.pdf', 'application/pdf'
    elif content.startswith(b'PK'):
        try:
            with ZipFile(BytesIO(content)) as archive:
                info = archive.infolist()
                if len(info) > 10000 or sum(entry.file_size for entry in info) > 100 * 1024 * 1024:
                    raise ValueError('archive_expansion_limit_exceeded')
                if archive.testzip() is not None:
                    raise ValueError('invalid_zip_document')
                names = set(archive.namelist())
                if '[Content_Types].xml' in names and 'word/document.xml' in names:
                    extension, mime = '.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
                elif '[Content_Types].xml' in names and 'xl/workbook.xml' in names:
                    extension, mime = '.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                else:
                    extension, mime = '.zip', 'application/zip'
        except (BadZipFile, RuntimeError, OSError) as exc:
            raise ValueError('invalid_zip_document') from exc
    elif (content.startswith(bytes.fromhex('D0CF11E0A1B11AE1')) and len(content) >= 512
          and content[28:30] == b'\xff\xfe' and Path(name).suffix.lower() in {'.doc', '.xls'}):
        # Legacy OLE documents: signature/envelope only, not semantic parsing.
        extension = Path(name).suffix.lower()
        mime = 'application/msword' if extension == '.doc' else 'application/vnd.ms-excel'
    else:
        raise ValueError('unsupported_document_content')
    stem = Path(name).stem if Path(name).suffix else name
    name = f'{stem or "documento"}{extension}'
    return DocumentPayload(content, name, mime, extension)


def atomic_write_document(path: Path, content: bytes, *, preserve_partial: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(content).digest():
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.document-', delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and not preserve_partial:
            temporary.unlink(missing_ok=True)

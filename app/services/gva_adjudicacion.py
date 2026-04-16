from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, Optional, Sequence
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

DISCOVERY_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicacion3"

DEFAULT_ALLOWED_SECTIONS = (
    "Cuerpo de Maestros",
    "Cuerpos de Secundaria y Otros Cuerpos",
)


@dataclass(slots=True)
class PdfCandidate:
    title: str
    url: str
    canonical_url: str
    section: Optional[str]
    source_page: str


@dataclass(slots=True)
class DownloadedPdf:
    url: str
    canonical_url: str
    original_filename: str
    stored_filename: str
    file_path: str
    content_type: Optional[str]
    size_bytes: int
    sha256: str
    status: Literal["new_version_saved", "already_known_hash"]


class GvaAdjudicacionService:
    def __init__(
        self,
        discovery_url: str = DISCOVERY_URL,
        timeout: float = 20.0,
        allowed_sections: Optional[Sequence[str]] = None,
        download_dir: str | Path = "data/adjudicacion3",
    ) -> None:
        self.discovery_url = discovery_url
        self.timeout = timeout
        self.allowed_sections = tuple(allowed_sections or DEFAULT_ALLOWED_SECTIONS)

        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.files_dir = self.download_dir / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)

        self.index_path = self.download_dir / "index.json"

        self.headers = {
            "User-Agent": "RadarDocentCV/0.1 (+https://ceice.gva.es/)"
        }

    def discover_pdf_links(self) -> list[PdfCandidate]:
        html = self._fetch_html(self.discovery_url)
        soup = BeautifulSoup(html, "html.parser")

        results: list[PdfCandidate] = []
        seen: set[tuple[str, str, str]] = set()

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            absolute_url = urljoin(self.discovery_url, href)
            anchor_text = self._clean_text(anchor.get_text(" ", strip=True))
            section = self._find_section(anchor)

            if not self._is_allowed_section(section):
                continue

            if not self._is_pdf_candidate(absolute_url, anchor_text):
                continue

            canonical_url = self._canonicalize_url(absolute_url)

            dedupe_key = (
                section or "",
                anchor_text,
                canonical_url,
            )
            if dedupe_key in seen:
                continue

            seen.add(dedupe_key)

            results.append(
                PdfCandidate(
                    title=anchor_text or self._filename_from_url(absolute_url),
                    url=absolute_url,
                    canonical_url=canonical_url,
                    section=section,
                    source_page=self.discovery_url,
                )
            )

        return results

    def download_unique_pdfs(
        self,
        candidates: list[PdfCandidate],
    ) -> list[DownloadedPdf]:
        downloads: list[DownloadedPdf] = []
        seen_urls: set[str] = set()

        index = self._load_index()

        for candidate in candidates:
            if candidate.canonical_url in seen_urls:
                continue

            seen_urls.add(candidate.canonical_url)
            result = self._download_and_index_pdf(candidate, index)
            downloads.append(result)

        self._save_index(index)
        return downloads

    def save_discovery_snapshot(
        self,
        candidates: list[PdfCandidate],
        filename: str = "discovered_links.json",
    ) -> Path:
        output_path = self.download_dir / filename
        payload = [asdict(item) for item in candidates]
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path

    def save_download_report(
        self,
        downloads: list[DownloadedPdf],
        filename: str = "download_report.json",
    ) -> Path:
        output_path = self.download_dir / filename
        payload = [asdict(item) for item in downloads]
        output_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output_path

    def _download_and_index_pdf(
        self,
        candidate: PdfCandidate,
        index: dict,
    ) -> DownloadedPdf:
        response = httpx.get(
            candidate.url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()

        content = response.content
        sha256 = self._sha256_bytes(content)
        content_type = response.headers.get("content-type")
        size_bytes = len(content)
        original_filename = self._filename_from_url(candidate.canonical_url)
        now = self._utc_now_iso()

        existing_record = self._find_record_by_hash(index, sha256)

        if existing_record is not None:
            self._touch_record_source(existing_record, candidate, now)

            return DownloadedPdf(
                url=candidate.url,
                canonical_url=candidate.canonical_url,
                original_filename=original_filename,
                stored_filename=existing_record["stored_filename"],
                file_path=existing_record["file_path"],
                content_type=existing_record.get("content_type") or content_type,
                size_bytes=existing_record.get("size_bytes", size_bytes),
                sha256=sha256,
                status="already_known_hash",
            )

        stored_filename = self._build_versioned_filename(
            original_filename=original_filename,
            sha256=sha256,
        )
        file_path = self.files_dir / stored_filename
        file_path.write_bytes(content)

        new_record = {
            "sha256": sha256,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "original_filename": original_filename,
            "stored_filename": stored_filename,
            "file_path": str(file_path),
            "first_seen_at": now,
            "last_seen_at": now,
            "sources": [
                {
                    "section": candidate.section,
                    "title": candidate.title,
                    "url": candidate.url,
                    "canonical_url": candidate.canonical_url,
                    "source_page": candidate.source_page,
                    "first_seen_at": now,
                    "last_seen_at": now,
                }
            ],
        }
        index["versions"].append(new_record)

        return DownloadedPdf(
            url=candidate.url,
            canonical_url=candidate.canonical_url,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status="new_version_saved",
        )

    def _load_index(self) -> dict:
        if not self.index_path.exists():
            return {"versions": []}

        payload = json.loads(self.index_path.read_text(encoding="utf-8"))

        if not isinstance(payload, dict):
            return {"versions": []}

        versions = payload.get("versions")
        if not isinstance(versions, list):
            return {"versions": []}

        return payload

    def _save_index(self, index: dict) -> None:
        self.index_path.write_text(
            json.dumps(index, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _find_record_by_hash(self, index: dict, sha256: str) -> Optional[dict]:
        for record in index.get("versions", []):
            if record.get("sha256") == sha256:
                return record
        return None

    def _touch_record_source(
        self,
        record: dict,
        candidate: PdfCandidate,
        seen_at: str,
    ) -> None:
        record["last_seen_at"] = seen_at

        sources = record.setdefault("sources", [])

        for source in sources:
            if (
                source.get("section") == candidate.section
                and source.get("title") == candidate.title
                and source.get("canonical_url") == candidate.canonical_url
            ):
                source["last_seen_at"] = seen_at
                return

        sources.append(
            {
                "section": candidate.section,
                "title": candidate.title,
                "url": candidate.url,
                "canonical_url": candidate.canonical_url,
                "source_page": candidate.source_page,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
        )

    def _fetch_html(self, url: str) -> str:
        response = httpx.get(
            url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def _is_allowed_section(self, section: Optional[str]) -> bool:
        if not section:
            return False
        return section in self.allowed_sections

    def _is_pdf_candidate(self, url: str, anchor_text: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        text = anchor_text.lower()

        strong_url_signals = (
            path.endswith(".pdf")
            or ".pdf/" in path
            or "/documents/" in path
        )

        strong_text_signals = any(
            phrase in text
            for phrase in (
                "resolución",
                "resolucion",
                "listado de adjudicación",
                "listado adjudicación",
                "listado de adjudicacion",
                "listado adjudicacion",
                "listado",
            )
        )

        return strong_url_signals or strong_text_signals

    def _canonicalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                "",
                "",
            )
        )

    def _filename_from_url(self, url: str) -> str:
        path = urlparse(url).path.rstrip("/")
        name = path.split("/")[-1] if path else "documento.pdf"
        return name or "documento.pdf"

    def _build_versioned_filename(
        self,
        original_filename: str,
        sha256: str,
    ) -> str:
        path = Path(original_filename)
        stem = self._slugify(path.stem or "documento")
        suffix = path.suffix.lower() or ".pdf"
        return f"{stem}__{sha256[:12]}{suffix}"

    def _slugify(self, value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
        value = re.sub(r"[-\s]+", "-", value, flags=re.UNICODE)
        return value.strip("-") or "documento"

    def _sha256_bytes(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _find_section(self, anchor: Tag) -> Optional[str]:
        previous = anchor.find_previous(["h1", "h2", "h3", "h4", "strong", "b"])
        if not previous:
            return None

        text = self._clean_text(previous.get_text(" ", strip=True))
        return text or None

    def _clean_text(self, value: str) -> str:
        return " ".join(value.split())
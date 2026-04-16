from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Literal, Optional

import httpx

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


@dataclass(slots=True)
class SyncedAsset:
    source_key: str
    asset_role: str
    title: str
    url: str
    canonical_url: str
    section: Optional[str]
    publication_date_text: Optional[str]
    downloadable: bool
    original_filename: Optional[str]
    stored_filename: Optional[str]
    file_path: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    sha256: Optional[str]
    status: Literal[
        "new_version_saved",
        "already_known_hash",
        "same_run_duplicate",
        "non_downloadable",
    ]


class DocumentSyncService:
    def __init__(self, base_dir: str | Path = "data", timeout: float = 30.0) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.headers = {
            "User-Agent": "RadarDocentCV/0.2 (+https://ceice.gva.es/)"
        }

    def sync_adapter(self, adapter: BaseDiscoveryAdapter) -> dict:
        started_at = self._utc_now_iso()
        source_dir = self.base_dir / adapter.source_key
        files_dir = source_dir / "files"

        source_dir.mkdir(parents=True, exist_ok=True)
        files_dir.mkdir(parents=True, exist_ok=True)

        index_path = source_dir / "index.json"
        discovered_path = source_dir / "discovered_assets.json"
        report_path = source_dir / "sync_report.json"
        runs_path = source_dir / "sync_runs.json"

        assets = adapter.discover_assets()
        discovered_path.write_text(
            json.dumps([asdict(item) for item in assets], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        index = self._load_json(index_path, default={"versions": []})
        results: list[SyncedAsset] = []
        processed_urls: dict[str, SyncedAsset] = {}

        for asset in assets:
            if not asset.downloadable:
                results.append(
                    SyncedAsset(
                        source_key=asset.source_key,
                        asset_role=asset.asset_role,
                        title=asset.title,
                        url=asset.url,
                        canonical_url=asset.canonical_url,
                        section=asset.section,
                        publication_date_text=asset.publication_date_text,
                        downloadable=False,
                        original_filename=None,
                        stored_filename=None,
                        file_path=None,
                        content_type=None,
                        size_bytes=None,
                        sha256=None,
                        status="non_downloadable",
                    )
                )
                continue

            if asset.canonical_url in processed_urls:
                previous = processed_urls[asset.canonical_url]
                results.append(replace(previous, status="same_run_duplicate", title=asset.title, section=asset.section))
                continue

            synced = self._download_and_index_asset(
                asset=asset,
                index=index,
                files_dir=files_dir,
            )
            processed_urls[asset.canonical_url] = synced
            results.append(synced)

        self._write_json(index_path, index)
        self._write_json(report_path, [asdict(item) for item in results])

        finished_at = self._utc_now_iso()
        runs_payload = self._load_json(runs_path, default={"runs": []})
        runs_payload["runs"].append(
            {
                "started_at": started_at,
                "finished_at": finished_at,
                "source_key": adapter.source_key,
                "source_url": adapter.source_url,
                "summary": {
                    "discovered_assets_count": len(assets),
                    "downloadable_assets_count": sum(1 for item in assets if item.downloadable),
                    "new_versions_count": sum(1 for item in results if item.status == "new_version_saved"),
                    "known_versions_count": sum(1 for item in results if item.status == "already_known_hash"),
                    "same_run_duplicate_count": sum(1 for item in results if item.status == "same_run_duplicate"),
                    "non_downloadable_count": sum(1 for item in results if item.status == "non_downloadable"),
                },
                "results": [asdict(item) for item in results],
            }
        )
        self._write_json(runs_path, runs_payload)

        return {
            "source_key": adapter.source_key,
            "source_url": adapter.source_url,
            "started_at": started_at,
            "finished_at": finished_at,
            "source_dir": str(source_dir),
            "discovered_assets_count": len(assets),
            "downloadable_assets_count": sum(1 for item in assets if item.downloadable),
            "new_versions_count": sum(1 for item in results if item.status == "new_version_saved"),
            "known_versions_count": sum(1 for item in results if item.status == "already_known_hash"),
            "same_run_duplicate_count": sum(1 for item in results if item.status == "same_run_duplicate"),
            "non_downloadable_count": sum(1 for item in results if item.status == "non_downloadable"),
        }

    def _download_and_index_asset(
        self,
        asset: DiscoveredAsset,
        index: dict,
        files_dir: Path,
    ) -> SyncedAsset:
        response = httpx.get(
            asset.url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()

        content = response.content
        sha256 = hashlib.sha256(content).hexdigest()
        content_type = response.headers.get("content-type")
        size_bytes = len(content)
        original_filename = self._filename_from_url(asset.canonical_url)
        seen_at = self._utc_now_iso()

        existing_record = self._find_record_by_hash(index, sha256)
        if existing_record is not None:
            self._touch_record_source(existing_record, asset, seen_at)
            return SyncedAsset(
                source_key=asset.source_key,
                asset_role=asset.asset_role,
                title=asset.title,
                url=asset.url,
                canonical_url=asset.canonical_url,
                section=asset.section,
                publication_date_text=asset.publication_date_text,
                downloadable=True,
                original_filename=original_filename,
                stored_filename=existing_record["stored_filename"],
                file_path=existing_record["file_path"],
                content_type=existing_record.get("content_type") or content_type,
                size_bytes=existing_record.get("size_bytes", size_bytes),
                sha256=sha256,
                status="already_known_hash",
            )

        stored_filename = self._build_versioned_filename(original_filename, sha256)
        file_path = files_dir / stored_filename
        file_path.write_bytes(content)

        record = {
            "sha256": sha256,
            "size_bytes": size_bytes,
            "content_type": content_type,
            "original_filename": original_filename,
            "stored_filename": stored_filename,
            "file_path": str(file_path),
            "first_seen_at": seen_at,
            "last_seen_at": seen_at,
            "sources": [
                {
                    "source_key": asset.source_key,
                    "source_url": asset.source_url,
                    "asset_role": asset.asset_role,
                    "title": asset.title,
                    "section": asset.section,
                    "url": asset.url,
                    "canonical_url": asset.canonical_url,
                    "publication_label": asset.publication_label,
                    "publication_date_text": asset.publication_date_text,
                    "first_seen_at": seen_at,
                    "last_seen_at": seen_at,
                }
            ],
        }
        index["versions"].append(record)

        return SyncedAsset(
            source_key=asset.source_key,
            asset_role=asset.asset_role,
            title=asset.title,
            url=asset.url,
            canonical_url=asset.canonical_url,
            section=asset.section,
            publication_date_text=asset.publication_date_text,
            downloadable=True,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status="new_version_saved",
        )

    def _touch_record_source(self, record: dict, asset: DiscoveredAsset, seen_at: str) -> None:
        record["last_seen_at"] = seen_at
        sources = record.setdefault("sources", [])

        for source in sources:
            if (
                source.get("source_key") == asset.source_key
                and source.get("asset_role") == asset.asset_role
                and source.get("canonical_url") == asset.canonical_url
                and source.get("section") == asset.section
            ):
                source["last_seen_at"] = seen_at
                return

        sources.append(
            {
                "source_key": asset.source_key,
                "source_url": asset.source_url,
                "asset_role": asset.asset_role,
                "title": asset.title,
                "section": asset.section,
                "url": asset.url,
                "canonical_url": asset.canonical_url,
                "publication_label": asset.publication_label,
                "publication_date_text": asset.publication_date_text,
                "first_seen_at": seen_at,
                "last_seen_at": seen_at,
            }
        )

    def _find_record_by_hash(self, index: dict, sha256: str) -> dict | None:
        for record in index.get("versions", []):
            if record.get("sha256") == sha256:
                return record
        return None

    def _load_json(self, path: Path, default: dict | list) -> dict | list:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default

    def _write_json(self, path: Path, payload: dict | list) -> None:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _filename_from_url(self, url: str) -> str:
        path = Path(re.sub(r"/+$", "", url.split("?", 1)[0].split("#", 1)[0]))
        name = path.name or "documento.pdf"
        return name

    def _build_versioned_filename(self, original_filename: str, sha256: str) -> str:
        path = Path(original_filename)
        stem = self._slugify(path.stem or "documento")
        suffix = path.suffix.lower() or ".pdf"
        return f"{stem}__{sha256[:12]}{suffix}"

    def _slugify(self, value: str) -> str:
        value = value.lower().strip()
        value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
        value = re.sub(r"[-\s]+", "-", value, flags=re.UNICODE)
        return value.strip("-") or "documento"

    def _utc_now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat()
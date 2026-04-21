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
from app.storage.sync_store import SyncStore


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
    document_version_id: Optional[int]
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
        "download_error",
    ]
    error_message: Optional[str] = None


class DocumentSyncService:
    def __init__(self, base_dir: str | Path = "data", timeout: float = 30.0) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.headers = {
            "User-Agent": "RadarDocentCV/0.6 (+https://ceice.gva.es/)"
        }

    def sync_adapter(self, adapter: BaseDiscoveryAdapter) -> dict:
        started_at = self._utc_now_iso()
        source_dir = self.base_dir / adapter.source_key
        files_dir = source_dir / "files"

        source_dir.mkdir(parents=True, exist_ok=True)
        files_dir.mkdir(parents=True, exist_ok=True)

        discovered_path = source_dir / "discovered_assets.json"
        report_path = source_dir / "sync_report.json"

        store = SyncStore()

        assets: list[DiscoveredAsset] = []
        results: list[SyncedAsset] = []
        processed_urls: dict[str, SyncedAsset] = {}

        source_id = store.ensure_source(
            source_key=adapter.source_key,
            source_url=adapter.source_url,
            label=adapter.source_label or adapter.source_key,
        )
        run_id = store.create_sync_run(
            source_id=source_id,
            started_at=started_at,
        )

        try:
            assets = adapter.discover_assets()

            self._write_json(
                discovered_path,
                [asdict(item) for item in assets],
            )

            for asset in assets:
                asset_id = store.create_asset(
                    source_id=source_id,
                    sync_run_id=run_id,
                    asset=asset,
                )

                if not asset.downloadable:
                    result = SyncedAsset(
                        source_key=asset.source_key,
                        asset_role=asset.asset_role,
                        title=asset.title,
                        url=asset.url,
                        canonical_url=asset.canonical_url,
                        section=asset.section,
                        publication_date_text=asset.publication_date_text,
                        downloadable=False,
                        document_version_id=None,
                        original_filename=None,
                        stored_filename=None,
                        file_path=None,
                        content_type=None,
                        size_bytes=None,
                        sha256=None,
                        status="non_downloadable",
                    )
                    results.append(result)
                    processed_urls[asset.canonical_url] = result
                    continue

                if asset.canonical_url in processed_urls:
                    previous = processed_urls[asset.canonical_url]

                    if previous.document_version_id is not None:
                        store.set_asset_document_version(
                            asset_id=asset_id,
                            document_version_id=previous.document_version_id,
                        )

                    results.append(
                        replace(
                            previous,
                            asset_role=asset.asset_role,
                            title=asset.title,
                            url=asset.url,
                            canonical_url=asset.canonical_url,
                            section=asset.section,
                            publication_date_text=asset.publication_date_text,
                            status="same_run_duplicate",
                        )
                    )
                    continue

                try:
                    synced = self._download_and_store_asset(
                        asset=asset,
                        files_dir=files_dir,
                        store=store,
                    )
                except Exception as exc:
                    synced = SyncedAsset(
                        source_key=asset.source_key,
                        asset_role=asset.asset_role,
                        title=asset.title,
                        url=asset.url,
                        canonical_url=asset.canonical_url,
                        section=asset.section,
                        publication_date_text=asset.publication_date_text,
                        downloadable=True,
                        document_version_id=None,
                        original_filename=self._filename_from_url(asset.canonical_url),
                        stored_filename=None,
                        file_path=None,
                        content_type=None,
                        size_bytes=None,
                        sha256=None,
                        status="download_error",
                        error_message=str(exc),
                    )

                if synced.document_version_id is not None:
                    store.set_asset_document_version(
                        asset_id=asset_id,
                        document_version_id=synced.document_version_id,
                    )

                processed_urls[asset.canonical_url] = synced
                results.append(synced)

            self._write_json(
                report_path,
                [asdict(item) for item in results],
            )

            finished_at = self._utc_now_iso()
            store.finish_sync_run(
                run_id=run_id,
                finished_at=finished_at,
                status="success",
                discovered_assets_count=len(assets),
                downloadable_assets_count=sum(1 for item in assets if item.downloadable),
                new_versions_count=sum(1 for item in results if item.status == "new_version_saved"),
                known_versions_count=sum(1 for item in results if item.status == "already_known_hash"),
                duplicate_assets_count=sum(1 for item in results if item.status == "same_run_duplicate"),
                non_downloadable_count=sum(
                    1
                    for item in results
                    if item.status in {"non_downloadable", "download_error"}
                ),
                error_message=None,
            )

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
                "download_error_count": sum(1 for item in results if item.status == "download_error"),
            }

        except Exception as exc:
            finished_at = self._utc_now_iso()

            self._write_json(
                report_path,
                [asdict(item) for item in results],
            )

            store.finish_sync_run(
                run_id=run_id,
                finished_at=finished_at,
                status="failed",
                discovered_assets_count=len(assets),
                downloadable_assets_count=sum(1 for item in assets if item.downloadable),
                new_versions_count=sum(1 for item in results if item.status == "new_version_saved"),
                known_versions_count=sum(1 for item in results if item.status == "already_known_hash"),
                duplicate_assets_count=sum(1 for item in results if item.status == "same_run_duplicate"),
                non_downloadable_count=sum(
                    1
                    for item in results
                    if item.status in {"non_downloadable", "download_error"}
                ),
                error_message=str(exc),
            )
            raise

        finally:
            store.close()

    def _download_and_store_asset(
        self,
        asset: DiscoveredAsset,
        files_dir: Path,
        store: SyncStore,
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
        downloaded_at = self._utc_now_iso()

        existing_version = store.get_document_version_by_sha256(sha256)
        if existing_version is not None:
            return SyncedAsset(
                source_key=asset.source_key,
                asset_role=asset.asset_role,
                title=asset.title,
                url=asset.url,
                canonical_url=asset.canonical_url,
                section=asset.section,
                publication_date_text=asset.publication_date_text,
                downloadable=True,
                document_version_id=int(existing_version["id"]),
                original_filename=original_filename,
                stored_filename=existing_version["stored_filename"],
                file_path=existing_version["file_path"],
                content_type=existing_version["content_type"] or content_type,
                size_bytes=existing_version["size_bytes"],
                sha256=sha256,
                status="already_known_hash",
            )

        stored_filename = self._build_versioned_filename(original_filename, sha256)
        file_path = files_dir / stored_filename
        file_path.write_bytes(content)

        document_version_id = store.create_document_version(
            sha256=sha256,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
            content_type=content_type,
            size_bytes=size_bytes,
            downloaded_at=downloaded_at,
        )

        return SyncedAsset(
            source_key=asset.source_key,
            asset_role=asset.asset_role,
            title=asset.title,
            url=asset.url,
            canonical_url=asset.canonical_url,
            section=asset.section,
            publication_date_text=asset.publication_date_text,
            downloadable=True,
            document_version_id=document_version_id,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_path=str(file_path),
            content_type=content_type,
            size_bytes=size_bytes,
            sha256=sha256,
            status="new_version_saved",
        )

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
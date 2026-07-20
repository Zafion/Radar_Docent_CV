from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.services.discovery.base import DiscoveredAsset
from app.storage.sync_store import SyncStore


DEFAULT_INCOMING_DIR = Path("/srv/funkcionario/remote_fetch/incoming/current")
DEFAULT_DATA_DIR = Path("data")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def is_safe_relative_path(path_value: str) -> bool:
    path = Path(path_value)
    return not path.is_absolute() and ".." not in path.parts


def require_safe_incoming_file(incoming_dir: Path, relative_file_path: str) -> Path:
    if not relative_file_path:
        raise ValueError("relative_file_path vacío")

    if not is_safe_relative_path(relative_file_path):
        raise ValueError(f"Ruta relativa no segura: {relative_file_path}")

    file_path = incoming_dir / relative_file_path

    if not file_path.exists():
        raise FileNotFoundError(f"No existe el fichero recibido: {file_path}")

    if not file_path.is_file():
        raise ValueError(f"No es un fichero regular: {file_path}")

    return file_path


def build_asset(asset_payload: dict) -> DiscoveredAsset:
    return DiscoveredAsset(
        source_key=asset_payload["source_key"],
        source_url=asset_payload["source_url"],
        publication_label=asset_payload.get("publication_label"),
        publication_date_text=asset_payload.get("publication_date_text"),
        asset_role=asset_payload["asset_role"],
        title=asset_payload["title"],
        url=asset_payload["url"],
        canonical_url=asset_payload["canonical_url"],
        section=asset_payload.get("section"),
        downloadable=bool(asset_payload["downloadable"]),
    )


def import_manifest(*, manifest_path: Path, incoming_dir: Path, data_dir: Path) -> dict:
    manifest = read_json(manifest_path)

    if manifest.get("schema_version") != 1:
        raise ValueError(f"schema_version no soportado: {manifest.get('schema_version')}")

    store = SyncStore()

    global_summary = {
        "sources_count": 0,
        "assets_count": 0,
        "new_versions_count": 0,
        "known_versions_count": 0,
        "duplicate_assets_count": 0,
        "non_downloadable_count": 0,
        "download_error_count": 0,
        "sources": [],
    }

    try:
        for source_payload in manifest.get("sources", []):
            source_key = source_payload["source_key"]
            source_url = source_payload["source_url"]
            source_label = source_payload.get("source_label") or source_key

            print("=" * 100)
            print(f"Importando source: {source_key}")
            print(f"URL:               {source_url}")

            source_id = store.ensure_source(
                source_key=source_key,
                source_url=source_url,
                label=source_label,
            )

            run_id = store.create_sync_run(
                source_id=source_id,
                started_at=source_payload.get("started_at") or utc_now_iso(),
            )

            results = source_payload.get("results", [])

            source_summary = {
                "source_key": source_key,
                "assets_count": 0,
                "new_versions_count": 0,
                "known_versions_count": 0,
                "duplicate_assets_count": 0,
                "non_downloadable_count": 0,
                "download_error_count": 0,
            }

            processed_canonical_urls: dict[str, int] = {}

            for result in results:
                asset_payload = result["asset"]
                asset = build_asset(asset_payload)

                asset_id = store.create_asset(
                    source_id=source_id,
                    sync_run_id=run_id,
                    asset=asset,
                )

                source_summary["assets_count"] += 1

                status = result.get("status")

                if status == "same_run_duplicate":
                    source_summary["duplicate_assets_count"] += 1

                if status == "non_downloadable":
                    source_summary["non_downloadable_count"] += 1
                    continue

                if status in {"download_error", "download_error_cached"}:
                    source_summary["download_error_count"] += 1
                    source_summary["non_downloadable_count"] += 1
                    continue

                if not asset.downloadable:
                    source_summary["non_downloadable_count"] += 1
                    continue

                sha256 = result.get("sha256")
                if not sha256:
                    source_summary["download_error_count"] += 1
                    source_summary["non_downloadable_count"] += 1
                    continue

                canonical_url = asset.canonical_url

                if canonical_url in processed_canonical_urls:
                    store.set_asset_document_version(
                        asset_id=asset_id,
                        document_version_id=processed_canonical_urls[canonical_url],
                    )
                    continue

                existing = store.get_document_version_by_sha256(sha256)

                if existing is not None:
                    document_version_id = int(existing["id"])
                    source_summary["known_versions_count"] += 1
                    store.set_asset_document_version(
                        asset_id=asset_id,
                        document_version_id=document_version_id,
                    )
                    processed_canonical_urls[canonical_url] = document_version_id
                    continue

                relative_file_path = result.get("relative_file_path")
                incoming_file = require_safe_incoming_file(incoming_dir, relative_file_path)

                stored_filename = result["stored_filename"]
                original_filename = result["original_filename"]
                content_type = result.get("content_type")
                size_bytes = int(result.get("size_bytes") or incoming_file.stat().st_size)
                downloaded_at = result.get("downloaded_at") or utc_now_iso()

                files_dir = data_dir / source_key / "files"
                files_dir.mkdir(parents=True, exist_ok=True)

                destination_file = files_dir / stored_filename
                if not destination_file.exists():
                    shutil.copy2(incoming_file, destination_file)

                document_version_id = store.create_document_version(
                    sha256=sha256,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    file_path=str(destination_file),
                    content_type=content_type,
                    size_bytes=size_bytes,
                    downloaded_at=downloaded_at,
                )

                source_summary["new_versions_count"] += 1

                store.set_asset_document_version(
                    asset_id=asset_id,
                    document_version_id=document_version_id,
                )
                processed_canonical_urls[canonical_url] = document_version_id

            store.finish_sync_run(
                run_id=run_id,
                finished_at=source_payload.get("finished_at") or utc_now_iso(),
                status="success",
                discovered_assets_count=len(source_payload.get("assets", [])),
                downloadable_assets_count=sum(
                    1
                    for result in results
                    if result.get("asset", {}).get("downloadable")
                ),
                new_versions_count=source_summary["new_versions_count"],
                known_versions_count=source_summary["known_versions_count"],
                duplicate_assets_count=source_summary["duplicate_assets_count"],
                non_downloadable_count=source_summary["non_downloadable_count"],
                error_message=None,
            )

            global_summary["sources_count"] += 1
            global_summary["assets_count"] += source_summary["assets_count"]
            global_summary["new_versions_count"] += source_summary["new_versions_count"]
            global_summary["known_versions_count"] += source_summary["known_versions_count"]
            global_summary["duplicate_assets_count"] += source_summary["duplicate_assets_count"]
            global_summary["non_downloadable_count"] += source_summary["non_downloadable_count"]
            global_summary["download_error_count"] += source_summary["download_error_count"]
            global_summary["sources"].append(source_summary)

            print(f"Assets importados:       {source_summary['assets_count']}")
            print(f"Nuevas versiones:        {source_summary['new_versions_count']}")
            print(f"Versiones conocidas:     {source_summary['known_versions_count']}")
            print(f"Duplicados misma run:    {source_summary['duplicate_assets_count']}")
            print(f"No descargables/errores: {source_summary['non_downloadable_count']}")

        store.connection.commit()
        return global_summary

    except Exception:
        store.connection.rollback()
        raise

    finally:
        store.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import CEICE files fetched from a remote/local fetcher into the production DB."
    )
    parser.add_argument(
        "--incoming-dir",
        default=str(DEFAULT_INCOMING_DIR),
        help="Directorio recibido por rsync. Default: /srv/funkcionario/remote_fetch/incoming/current",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA_DIR),
        help="Directorio data del proyecto. Default: data",
    )

    args = parser.parse_args()

    incoming_dir = Path(args.incoming_dir)
    manifest_path = incoming_dir / "manifest.json"
    data_dir = Path(args.data_dir)

    if not manifest_path.exists():
        raise SystemExit(f"No existe manifest: {manifest_path}")

    summary = import_manifest(
        manifest_path=manifest_path,
        incoming_dir=incoming_dir,
        data_dir=data_dir,
    )

    print("=" * 100)
    print("IMPORT REMOTE FETCH OK")
    print(f"Fuentes:              {summary['sources_count']}")
    print(f"Assets:               {summary['assets_count']}")
    print(f"Nuevas versiones:     {summary['new_versions_count']}")
    print(f"Versiones conocidas:  {summary['known_versions_count']}")
    print(f"Duplicados:           {summary['duplicate_assets_count']}")
    print(f"No descargables/error:{summary['non_downloadable_count']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

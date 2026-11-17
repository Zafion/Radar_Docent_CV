from __future__ import annotations

from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))

from app.services.discovery.base import DiscoveredAsset
from app.storage.sync_store import SyncStore
from app.services.remote_import_integrity import (
    ImportIntegrityError, validated_manifest, publish_snapshot, verify_existing_version,
)


DEFAULT_INCOMING_DIR = Path("/srv/funkcionario/remote_fetch/incoming/current")
DEFAULT_DATA_DIR = Path("data")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def import_manifest(*, manifest_path: Path, incoming_dir: Path, data_dir: Path, notification_intent: str = "historical_import") -> dict:
    if notification_intent not in {"live_new","historical_import","reprocess","unknown"}:
        raise ValueError("invalid_notification_ingestion_intent")
    with validated_manifest(manifest_path, incoming_dir, data_dir) as (manifest, snapshots):
        return _import_validated_manifest(manifest, snapshots, data_dir, notification_intent)


def _import_validated_manifest(manifest, snapshots, data_dir, notification_intent):
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
            print("Importando fuente validada")

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
                    verify_existing_version(existing, data_dir=data_dir, sha256=sha256, size_bytes=result['size_bytes'])
                    document_version_id = int(existing["id"])
                    source_summary["known_versions_count"] += 1
                    store.set_asset_document_version(
                        asset_id=asset_id,
                        document_version_id=document_version_id,
                    )
                    processed_canonical_urls[canonical_url] = document_version_id
                    continue

                stored_filename = result["stored_filename"]
                original_filename = result["original_filename"]
                content_type = result.get("content_type")
                size_bytes = result["size_bytes"]
                downloaded_at = result.get("downloaded_at") or utc_now_iso()
                destination_file = publish_snapshot(
                    snapshots[id(result)], data_dir=data_dir, source_key=source_key,
                    filename=stored_filename, sha256=sha256, size_bytes=size_bytes,
                )

                document_version_id = store.create_document_version(
                    sha256=sha256,
                    original_filename=original_filename,
                    stored_filename=stored_filename,
                    file_path=str(destination_file),
                    content_type=content_type,
                    size_bytes=size_bytes,
                    downloaded_at=downloaded_at,
                    notification_intent=notification_intent,
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
                status=source_payload["status"] if source_payload.get("status") in {"failed", "partial"} else "success",
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
                error_message=source_payload.get("error_message") if source_payload.get("status") in {"failed", "partial"} else None,
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

    parser.add_argument("--notification-origin",choices=("live_new","historical_import","reprocess","unknown"),default="historical_import")
    args = parser.parse_args()

    incoming_dir = Path(args.incoming_dir)
    manifest_path = incoming_dir / "manifest.json"
    data_dir = Path(args.data_dir)

    if not manifest_path.exists():
        raise SystemExit(f"No existe manifest: {manifest_path}")

    try:
        summary = import_manifest(
            manifest_path=manifest_path,
            incoming_dir=incoming_dir,
            data_dir=data_dir,
            notification_intent=args.notification_origin,
        )
    except ImportIntegrityError as exc:
        print(json.dumps({"status": "rejected", "rejected_batches": 1, "reason": exc.reason}))
        return 2

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

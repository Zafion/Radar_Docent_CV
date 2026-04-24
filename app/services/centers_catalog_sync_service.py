from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.centers_catalog_downloader import (
    CentersCatalogAuthorizationError,
    download_centers_catalog,
)
from app.services.centers_import_service import import_centers_catalog
from app.services.discovery.centers_catalog_auth import obtain_centers_catalog_token
from app.storage.centers_catalog_sync_store import insert_centers_catalog_sync_run
from app.storage.db import get_connection


class CentersCatalogSyncService:
    def sync(
        self,
        *,
        raw_dir: str | Path,
        cod_provincia: str = "",
        headless: bool = True,
    ) -> dict[str, Any]:
        raw_dir = Path(raw_dir)

        scope_dir = raw_dir / (cod_provincia or "all")
        scope_dir.mkdir(parents=True, exist_ok=True)

        started_at = datetime.now(timezone.utc).isoformat()
        summary: dict[str, Any] = {
            "started_at": started_at,
            "finished_at": None,
            "status": "failed",
            "cod_provincia": cod_provincia,
            "source_url": "https://ceice.gva.es/es/web/admision-alumnado/centres-educatius",
            "endpoint_url": "https://xacen-backend.gva.es/xacen-backend/api/v1/informe/ExcelListadoByProvincias",
            "output_dir": str(scope_dir),
            "json_path": None,
            "xlsx_path": None,
            "sha256_path": None,
            "sha256_value": None,
            "previous_sha256": None,
            "token_refresh_attempted": False,
            "downloaded_file_name": None,
            "downloaded_mime_type": None,
            "downloaded_size_bytes": None,
            "changed": False,
            "imported_rows": None,
            "centers_before": None,
            "centers_after": None,
            "error_message": None,
        }

        try:
            token = obtain_centers_catalog_token(headless=headless)

            try:
                download_summary = download_centers_catalog(
                    token=token,
                    raw_dir=scope_dir,
                    cod_provincia=cod_provincia,
                )
            except CentersCatalogAuthorizationError:
                summary["token_refresh_attempted"] = True
                refreshed_token = obtain_centers_catalog_token(headless=headless)
                download_summary = download_centers_catalog(
                    token=refreshed_token,
                    raw_dir=scope_dir,
                    cod_provincia=cod_provincia,
                )

            summary.update(
                {
                    "source_url": download_summary.get("source_page_url", summary["source_url"]),
                    "endpoint_url": download_summary.get("source_api_url", summary["endpoint_url"]),
                    "cod_provincia": download_summary.get("cod_provincia", cod_provincia),
                    "json_path": download_summary.get("response_json_path"),
                    "xlsx_path": download_summary.get("xlsx_path"),
                    "sha256_path": str(scope_dir / "sha256.txt"),
                    "sha256_value": download_summary.get("sha256"),
                    "previous_sha256": download_summary.get("previous_sha256"),
                    "downloaded_file_name": download_summary.get("response_filename"),
                    "downloaded_mime_type": download_summary.get("response_mime_type"),
                    "downloaded_size_bytes": download_summary.get("downloaded_size_bytes"),
                    "changed": bool(download_summary.get("changed")),
                }
            )

            if summary["changed"]:
                import_summary = import_centers_catalog(
                    xlsx_path=summary["xlsx_path"],
                )
                summary["imported_rows"] = import_summary["processed_rows"]
                summary["centers_before"] = import_summary["centers_before"]
                summary["centers_after"] = import_summary["centers_after"]
                summary["status"] = "imported"
            else:
                summary["status"] = "unchanged"

        except Exception as exc:  # noqa: BLE001
            summary["error_message"] = str(exc)
            summary["status"] = "failed"

        finally:
            summary["finished_at"] = datetime.now(timezone.utc).isoformat()
            conn = get_connection()
            try:
                insert_centers_catalog_sync_run(conn, summary)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn._conn.close()

        return summary
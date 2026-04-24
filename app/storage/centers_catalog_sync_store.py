from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_centers_catalog_sync_run(conn: Any, payload: dict[str, Any]) -> int:
    xlsx_path = payload.get("xlsx_path")
    output_dir = payload.get("output_dir")
    if output_dir is None and xlsx_path:
        output_dir = str(Path(xlsx_path).resolve().parent)

    started_at = payload.get("started_at") or _now_iso()
    finished_at = payload.get("finished_at") or started_at

    cursor = conn.execute(
        """
        INSERT INTO centers_catalog_sync_runs (
            started_at,
            finished_at,
            status,
            cod_provincia,
            source_url,
            endpoint_url,
            output_dir,
            json_path,
            xlsx_path,
            sha256_path,
            sha256_value,
            token_refresh_attempted,
            downloaded_file_name,
            downloaded_mime_type,
            downloaded_size_bytes,
            imported_rows,
            centers_before,
            centers_after,
            changed,
            error_message
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            started_at,
            finished_at,
            payload.get("status") or "unknown",
            payload.get("cod_provincia"),
            payload.get("source_url") or "",
            payload.get("endpoint_url") or "",
            output_dir or "",
            payload.get("json_path"),
            payload.get("xlsx_path"),
            payload.get("sha256_path"),
            payload.get("sha256_value"),
            bool(payload.get("token_refresh_attempted")),
            payload.get("downloaded_file_name"),
            payload.get("downloaded_mime_type"),
            payload.get("downloaded_size_bytes"),
            payload.get("imported_rows"),
            payload.get("centers_before"),
            payload.get("centers_after"),
            bool(payload.get("changed")),
            payload.get("error_message"),
        ),
    )
    return int(cursor.fetchone()[0])
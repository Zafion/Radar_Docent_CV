from __future__ import annotations

import sqlite3

from app.services.discovery.base import DiscoveredAsset
from app.storage.sqlite import get_connection


class SyncStore:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def ensure_source(
        self,
        source_key: str,
        source_url: str,
        label: str,
    ) -> int:
        row = self.connection.execute(
            """
            SELECT id
            FROM sources
            WHERE source_key = ?
            """,
            (source_key,),
        ).fetchone()

        if row is not None:
            self.connection.execute(
                """
                UPDATE sources
                SET source_url = ?, label = ?, is_active = 1
                WHERE id = ?
                """,
                (source_url, label, row["id"]),
            )
            self.connection.commit()
            return int(row["id"])

        cursor = self.connection.execute(
            """
            INSERT INTO sources (source_key, source_url, label, is_active)
            VALUES (?, ?, ?, 1)
            """,
            (source_key, source_url, label),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def create_sync_run(
        self,
        source_id: int,
        started_at: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO sync_runs (
                source_id,
                started_at,
                status
            )
            VALUES (?, ?, ?)
            """,
            (source_id, started_at, "running"),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_sync_run(
        self,
        run_id: int,
        finished_at: str,
        status: str,
        discovered_assets_count: int,
        downloadable_assets_count: int,
        new_versions_count: int,
        known_versions_count: int,
        duplicate_assets_count: int,
        non_downloadable_count: int,
        error_message: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE sync_runs
            SET
                finished_at = ?,
                status = ?,
                discovered_assets_count = ?,
                downloadable_assets_count = ?,
                new_versions_count = ?,
                known_versions_count = ?,
                duplicate_assets_count = ?,
                non_downloadable_count = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                discovered_assets_count,
                downloadable_assets_count,
                new_versions_count,
                known_versions_count,
                duplicate_assets_count,
                non_downloadable_count,
                error_message,
                run_id,
            ),
        )
        self.connection.commit()

    def create_asset(
        self,
        source_id: int,
        sync_run_id: int,
        asset: DiscoveredAsset,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO assets (
                source_id,
                sync_run_id,
                asset_role,
                title,
                section,
                publication_label,
                publication_date_text,
                url,
                canonical_url,
                is_downloadable
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                sync_run_id,
                asset.asset_role,
                asset.title,
                asset.section,
                asset.publication_label,
                asset.publication_date_text,
                asset.url,
                asset.canonical_url,
                1 if asset.downloadable else 0,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def set_asset_document_version(
        self,
        asset_id: int,
        document_version_id: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE assets
            SET document_version_id = ?
            WHERE id = ?
            """,
            (document_version_id, asset_id),
        )
        self.connection.commit()

    def get_document_version_by_sha256(
        self,
        sha256: str,
    ) -> sqlite3.Row | None:
        return self.connection.execute(
            """
            SELECT *
            FROM document_versions
            WHERE sha256 = ?
            """,
            (sha256,),
        ).fetchone()

    def create_document_version(
        self,
        sha256: str,
        original_filename: str,
        stored_filename: str,
        file_path: str,
        content_type: str | None,
        size_bytes: int,
        downloaded_at: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO document_versions (
                sha256,
                original_filename,
                stored_filename,
                file_path,
                content_type,
                size_bytes,
                downloaded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sha256,
                original_filename,
                stored_filename,
                file_path,
                content_type,
                size_bytes,
                downloaded_at,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)
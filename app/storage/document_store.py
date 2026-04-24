from __future__ import annotations

from typing import Any

from app.storage.db import get_connection


class DocumentStore:
    def __init__(self, connection: Any | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_unregistered_document_candidates(self) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            """
            SELECT
                a.id AS asset_id,
                a.asset_role,
                a.title AS asset_title,
                a.section,
                a.publication_label,
                a.publication_date_text,
                a.url,
                a.canonical_url,
                a.document_version_id,
                s.id AS source_id,
                s.source_key,
                s.label AS source_label,
                dv.original_filename,
                dv.stored_filename,
                dv.file_path,
                dv.sha256
            FROM assets a
            JOIN sources s
                ON s.id = a.source_id
            JOIN document_versions dv
                ON dv.id = a.document_version_id
            LEFT JOIN documents d
                ON d.document_version_id = dv.id
            WHERE a.document_version_id IS NOT NULL
              AND d.id IS NULL
            ORDER BY dv.id, a.id
            """
        ).fetchall()
        return [dict(row) for row in rows]

    def create_document(
        self,
        document_version_id: int,
        source_id: int,
        doc_family: str,
        title: str | None,
        document_date_text: str | None,
        document_date_iso: str | None,
        list_scope: str | None,
        notes: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO documents (
                document_version_id,
                source_id,
                doc_family,
                title,
                document_date_text,
                document_date_iso,
                list_scope,
                notes,
                parsed_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
            RETURNING id
            """,
            (
                document_version_id,
                source_id,
                doc_family,
                title,
                document_date_text,
                document_date_iso,
                list_scope,
                notes,
            ),
        )
        return int(cursor.fetchone()[0])
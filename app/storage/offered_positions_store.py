from __future__ import annotations

import sqlite3

from app.storage.sqlite import get_connection


class OfferedPositionsStore:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_offered_position_documents(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                d.id AS document_id,
                d.document_version_id,
                d.title,
                d.document_date_text,
                d.document_date_iso,
                d.list_scope,
                d.doc_family,
                dv.file_path,
                dv.original_filename,
                dv.sha256,
                s.source_key,
                s.label AS source_label
            FROM documents d
            JOIN document_versions dv
                ON dv.id = d.document_version_id
            JOIN sources s
                ON s.id = d.source_id
            WHERE d.doc_family = 'offered_positions'
            ORDER BY d.id
            """
        ).fetchall()

    def create_parse_run(
        self,
        document_version_id: int,
        parser_key: str,
        parser_version: str,
        started_at: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO document_parse_runs (
                document_version_id,
                parser_key,
                parser_version,
                status,
                started_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                document_version_id,
                parser_key,
                parser_version,
                "running",
                started_at,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_parse_run(
        self,
        parse_run_id: int,
        finished_at: str,
        status: str,
        rows_extracted: int,
        error_message: str | None = None,
    ) -> None:
        self.connection.execute(
            """
            UPDATE document_parse_runs
            SET
                finished_at = ?,
                status = ?,
                rows_extracted = ?,
                error_message = ?
            WHERE id = ?
            """,
            (
                finished_at,
                status,
                rows_extracted,
                error_message,
                parse_run_id,
            ),
        )
        self.connection.commit()

    def clear_offered_positions_for_document(self, document_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM offered_positions
            WHERE document_id = ?
            """,
            (document_id,),
        )
        self.connection.commit()

    def insert_offered_position(
        self,
        *,
        document_id: int,
        source_type: str,
        body_code: str | None,
        body_name: str | None,
        specialty_code: str | None,
        specialty_name: str | None,
        province: str | None,
        locality: str | None,
        center_code: str | None,
        center_name: str | None,
        position_code: str | None,
        hours_text: str | None,
        hours_value: float | None,
        is_itinerant: int | None,
        valenciano_required_text: str | None,
        position_type: str | None,
        composition: str | None,
        observations: str | None,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO offered_positions (
                document_id,
                source_type,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                province,
                locality,
                center_code,
                center_name,
                position_code,
                hours_text,
                hours_value,
                is_itinerant,
                valenciano_required_text,
                position_type,
                composition,
                observations,
                raw_row_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                source_type,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                province,
                locality,
                center_code,
                center_name,
                position_code,
                hours_text,
                hours_value,
                is_itinerant,
                valenciano_required_text,
                position_type,
                composition,
                observations,
                raw_row_text,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def mark_document_parsed(self, document_id: int, parsed_at: str) -> None:
        self.connection.execute(
            """
            UPDATE documents
            SET parsed_at = ?
            WHERE id = ?
            """,
            (parsed_at, document_id),
        )
        self.connection.commit()
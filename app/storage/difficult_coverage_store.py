from __future__ import annotations

import sqlite3

from app.storage.sqlite import get_connection


class DifficultCoverageStore:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_provisional_documents(
        self,
        *,
        parser_key: str,
        parser_version: str,
    ) -> list[sqlite3.Row]:
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
            WHERE d.doc_family = 'difficult_coverage_provisional'
              AND NOT EXISTS (
                  SELECT 1
                  FROM document_parse_runs pr
                  WHERE pr.document_version_id = d.document_version_id
                    AND pr.parser_key = ?
                    AND pr.parser_version = ?
                    AND pr.status = 'success'
              )
            ORDER BY d.id
            """,
            (parser_key, parser_version),
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

    def clear_for_document(self, document_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM difficult_coverage_candidates
            WHERE position_id IN (
                SELECT id
                FROM difficult_coverage_positions
                WHERE document_id = ?
            )
            """,
            (document_id,),
        )
        self.connection.execute(
            """
            DELETE FROM difficult_coverage_positions
            WHERE document_id = ?
            """,
            (document_id,),
        )
        self.connection.commit()

    def insert_position(
        self,
        *,
        document_id: int,
        body_code: str | None,
        body_name: str | None,
        specialty_code: str | None,
        specialty_name: str | None,
        position_code: str,
        center_code: str | None,
        center_name: str | None,
        locality: str | None,
        num_participants: int | None,
        sorteo_number: str | None,
        registro_superior: str | None,
        registro_inferior: str | None,
        raw_header_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO difficult_coverage_positions (
                document_id,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                position_code,
                center_code,
                center_name,
                locality,
                num_participants,
                sorteo_number,
                registro_superior,
                registro_inferior,
                raw_header_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                position_code,
                center_code,
                center_name,
                locality,
                num_participants,
                sorteo_number,
                registro_superior,
                registro_inferior,
                raw_header_text,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_candidate(
        self,
        *,
        position_id: int,
        row_number: int | None,
        is_selected: int,
        last_name_1: str | None,
        last_name_2: str | None,
        first_name: str | None,
        full_name: str,
        full_name_normalized: str,
        registration_datetime_text: str | None,
        registration_code_or_bag_order: str | None,
        petition_text: str | None,
        petition_number: int | None,
        has_master_text: str | None,
        valenciano_requirement_text: str | None,
        adjudication_group_text: str | None,
        assigned_position_code: str | None,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO difficult_coverage_candidates (
                position_id,
                row_number,
                is_selected,
                last_name_1,
                last_name_2,
                first_name,
                full_name,
                full_name_normalized,
                registration_datetime_text,
                registration_code_or_bag_order,
                petition_text,
                petition_number,
                has_master_text,
                valenciano_requirement_text,
                adjudication_group_text,
                assigned_position_code,
                raw_row_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                position_id,
                row_number,
                is_selected,
                last_name_1,
                last_name_2,
                first_name,
                full_name,
                full_name_normalized,
                registration_datetime_text,
                registration_code_or_bag_order,
                petition_text,
                petition_number,
                has_master_text,
                valenciano_requirement_text,
                adjudication_group_text,
                assigned_position_code,
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
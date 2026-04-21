from __future__ import annotations

import sqlite3

from app.storage.sqlite import get_connection


class AwardResultsStore:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_final_listing_documents(
        self,
        *,
        list_scope: str,
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
            WHERE d.doc_family = 'final_award_listing'
              AND d.list_scope = ?
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
            (list_scope, parser_key, parser_version),
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

    def clear_award_results_for_document(self, document_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM award_assignments
            WHERE award_result_id IN (
                SELECT id
                FROM award_results
                WHERE document_id = ?
            )
            """,
            (document_id,),
        )
        self.connection.execute(
            """
            DELETE FROM award_results
            WHERE document_id = ?
            """,
            (document_id,),
        )
        self.connection.commit()

    def insert_award_result(
        self,
        *,
        document_id: int,
        list_scope: str,
        body_code: str | None,
        body_name: str | None,
        specialty_code: str | None,
        specialty_name: str | None,
        order_number: int | None,
        person_display_name: str,
        person_name_normalized: str,
        status: str,
        raw_block_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO award_results (
                document_id,
                list_scope,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                order_number,
                person_display_name,
                person_name_normalized,
                status,
                raw_block_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                list_scope,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                order_number,
                person_display_name,
                person_name_normalized,
                status,
                raw_block_text,
            ),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def insert_award_assignment(
        self,
        *,
        award_result_id: int,
        assignment_kind: str | None,
        locality: str | None,
        center_code: str | None,
        center_name: str | None,
        position_specialty_code: str | None,
        position_specialty_name: str | None,
        position_code: str | None,
        hours_text: str | None,
        hours_value: float | None,
        petition_text: str | None,
        petition_number: int | None,
        request_type: str | None,
        matched_offered_position_id: int | None,
        raw_assignment_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO award_assignments (
                award_result_id,
                assignment_kind,
                locality,
                center_code,
                center_name,
                position_specialty_code,
                position_specialty_name,
                position_code,
                hours_text,
                hours_value,
                petition_text,
                petition_number,
                request_type,
                matched_offered_position_id,
                raw_assignment_text
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                award_result_id,
                assignment_kind,
                locality,
                center_code,
                center_name,
                position_specialty_code,
                position_specialty_name,
                position_code,
                hours_text,
                hours_value,
                petition_text,
                petition_number,
                request_type,
                matched_offered_position_id,
                raw_assignment_text,
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
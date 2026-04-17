from __future__ import annotations

import sqlite3

from app.storage.sqlite import get_connection


class AssignmentMatchingStore:
    def __init__(self, connection: sqlite3.Connection | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_unmatched_award_assignments(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT
                aa.id AS assignment_id,
                aa.position_code,
                aa.locality,
                aa.center_code,
                aa.center_name,
                aa.position_specialty_code,
                aa.position_specialty_name,
                ar.id AS award_result_id,
                ar.document_id AS award_document_id,
                d.source_id,
                d.document_date_iso,
                d.list_scope,
                s.source_key
            FROM award_assignments aa
            JOIN award_results ar
                ON ar.id = aa.award_result_id
            JOIN documents d
                ON d.id = ar.document_id
            JOIN sources s
                ON s.id = d.source_id
            WHERE aa.position_code IS NOT NULL
              AND aa.matched_offered_position_id IS NULL
            ORDER BY aa.id
            """
        ).fetchall()

    def find_candidate_offered_positions(
        self,
        *,
        source_id: int,
        document_date_iso: str | None,
        position_code: str,
    ) -> list[sqlite3.Row]:
        if document_date_iso:
            return self.connection.execute(
                """
                SELECT
                    op.id,
                    op.document_id,
                    op.source_type,
                    op.position_code,
                    op.locality,
                    op.center_code,
                    op.center_name,
                    op.specialty_code,
                    op.specialty_name,
                    d.document_date_iso
                FROM offered_positions op
                JOIN documents d
                    ON d.id = op.document_id
                WHERE d.source_id = ?
                  AND d.document_date_iso = ?
                  AND op.position_code = ?
                ORDER BY op.id
                """,
                (source_id, document_date_iso, position_code),
            ).fetchall()

        return self.connection.execute(
            """
            SELECT
                op.id,
                op.document_id,
                op.source_type,
                op.position_code,
                op.locality,
                op.center_code,
                op.center_name,
                op.specialty_code,
                op.specialty_name,
                d.document_date_iso
            FROM offered_positions op
            JOIN documents d
                ON d.id = op.document_id
            WHERE d.source_id = ?
              AND op.position_code = ?
            ORDER BY op.id
            """,
            (source_id, position_code),
        ).fetchall()

    def set_assignment_match(
        self,
        *,
        assignment_id: int,
        offered_position_id: int,
    ) -> None:
        self.connection.execute(
            """
            UPDATE award_assignments
            SET matched_offered_position_id = ?
            WHERE id = ?
            """,
            (offered_position_id, assignment_id),
        )
        self.connection.commit()
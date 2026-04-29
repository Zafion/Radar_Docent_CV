from __future__ import annotations

from typing import Any

from app.storage.db import get_connection


class NonDocentStore:
    def __init__(self, connection: Any | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_documents_for_parser(
        self,
        *,
        doc_families: tuple[str, ...],
        parser_key: str,
        parser_version: str,
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("%s" for _ in doc_families)
        rows = self.connection.execute(
            f"""
            SELECT
                d.id AS document_id,
                d.document_version_id,
                d.title,
                d.document_date_text,
                d.document_date_iso,
                d.list_scope,
                d.doc_family,
                d.notes,
                dv.file_path,
                dv.original_filename,
                dv.sha256,
                s.id AS source_id,
                s.source_key,
                s.source_url,
                s.label AS source_label,
                MIN(a.url) AS asset_url,
                MIN(a.canonical_url) AS asset_canonical_url,
                MIN(a.section) AS asset_section,
                MIN(a.publication_label) AS asset_publication_label,
                MIN(a.publication_date_text) AS asset_publication_date_text
            FROM documents d
            JOIN document_versions dv
                ON dv.id = d.document_version_id
            JOIN sources s
                ON s.id = d.source_id
            LEFT JOIN assets a
                ON a.document_version_id = d.document_version_id
            WHERE d.doc_family IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1
                  FROM document_parse_runs pr
                  WHERE pr.document_version_id = d.document_version_id
                    AND pr.parser_key = %s
                    AND pr.parser_version = %s
                    AND pr.status = 'success'
              )
            GROUP BY
                d.id,
                d.document_version_id,
                d.title,
                d.document_date_text,
                d.document_date_iso,
                d.list_scope,
                d.doc_family,
                d.notes,
                dv.file_path,
                dv.original_filename,
                dv.sha256,
                s.id,
                s.source_key,
                s.source_url,
                s.label
            ORDER BY d.id
            """,
            (*doc_families, parser_key, parser_version),
        ).fetchall()
        return [dict(row) for row in rows]

    def create_parse_run(
        self,
        *,
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
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (document_version_id, parser_key, parser_version, "running", started_at),
        )
        return int(cursor.fetchone()[0])

    def finish_parse_run(
        self,
        *,
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
                finished_at = %s,
                status = %s,
                rows_extracted = %s,
                error_message = %s
            WHERE id = %s
            """,
            (finished_at, status, rows_extracted, error_message, parse_run_id),
        )

    def mark_document_parsed(self, *, document_id: int, parsed_at: str) -> None:
        self.connection.execute(
            """
            UPDATE documents
            SET parsed_at = %s
            WHERE id = %s
            """,
            (parsed_at, document_id),
        )

    def get_staff_group_id_by_code(self, code: str | None) -> int | None:
        if not code:
            return None

        row = self.connection.execute(
            """
            SELECT id
            FROM non_docent_staff_groups
            WHERE code = %s
            """,
            (code,),
        ).fetchone()
        return int(row["id"]) if row is not None else None

    def upsert_publication(
        self,
        *,
        staff_group_id: int | None,
        document_id: int,
        publication_kind: str,
        publication_code: str | None,
        title: str,
        source_page_url: str | None,
        document_url: str | None,
        publication_date_text: str | None,
        publication_date_iso: str | None,
        status_text: str | None,
        notes: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO non_docent_publications (
                staff_group_id,
                document_id,
                publication_kind,
                publication_code,
                title,
                source_page_url,
                document_url,
                publication_date_text,
                publication_date_iso,
                status_text,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (document_id) DO UPDATE
            SET
                staff_group_id = EXCLUDED.staff_group_id,
                publication_kind = EXCLUDED.publication_kind,
                publication_code = EXCLUDED.publication_code,
                title = EXCLUDED.title,
                source_page_url = EXCLUDED.source_page_url,
                document_url = EXCLUDED.document_url,
                publication_date_text = EXCLUDED.publication_date_text,
                publication_date_iso = EXCLUDED.publication_date_iso,
                status_text = EXCLUDED.status_text,
                notes = EXCLUDED.notes,
                updated_at = NOW()
            RETURNING id
            """,
            (
                staff_group_id,
                document_id,
                publication_kind,
                publication_code,
                title,
                source_page_url,
                document_url,
                publication_date_text,
                publication_date_iso,
                status_text,
                notes,
            ),
        )
        return int(cursor.fetchone()[0])

    def clear_publication_rows(self, *, publication_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM non_docent_bag_members
            WHERE snapshot_id IN (
                SELECT id
                FROM non_docent_bag_snapshots
                WHERE publication_id = %s
            )
            """,
            (publication_id,),
        )
        self.connection.execute(
            "DELETE FROM non_docent_bag_snapshots WHERE publication_id = %s",
            (publication_id,),
        )
        self.connection.execute(
            "DELETE FROM non_docent_awards WHERE publication_id = %s",
            (publication_id,),
        )
        self.connection.execute(
            "DELETE FROM non_docent_offered_positions WHERE publication_id = %s",
            (publication_id,),
        )

    def insert_offered_position(
        self,
        *,
        publication_id: int,
        staff_group_id: int | None,
        position_code: str | None,
        classification: str | None,
        denomination: str | None,
        center_name: str | None,
        center_code: str | None,
        locality: str | None,
        province: str | None,
        occupancy_percent: float | None,
        functional_assignment: str | None,
        reason: str | None,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO non_docent_offered_positions (
                publication_id,
                staff_group_id,
                position_code,
                classification,
                denomination,
                center_name,
                center_code,
                locality,
                province,
                occupancy_percent,
                functional_assignment,
                reason,
                raw_row_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                publication_id,
                staff_group_id,
                position_code,
                classification,
                denomination,
                center_name,
                center_code,
                locality,
                province,
                occupancy_percent,
                functional_assignment,
                reason,
                raw_row_text,
            ),
        )
        return int(cursor.fetchone()[0])

    def insert_award(
        self,
        *,
        publication_id: int,
        staff_group_id: int | None,
        bag_code: str | None,
        bag_name: str | None,
        score: float | None,
        scope_text: str | None,
        person_display_name: str,
        person_name_normalized: str,
        career_official_text: str | None,
        position_code: str | None,
        position_text: str | None,
        locality: str | None,
        center_name: str | None,
        is_deserted: bool,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO non_docent_awards (
                publication_id,
                staff_group_id,
                bag_code,
                bag_name,
                score,
                scope_text,
                person_display_name,
                person_name_normalized,
                career_official_text,
                position_code,
                position_text,
                locality,
                center_name,
                is_deserted,
                raw_row_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                publication_id,
                staff_group_id,
                bag_code,
                bag_name,
                score,
                scope_text,
                person_display_name,
                person_name_normalized,
                career_official_text,
                position_code,
                position_text,
                locality,
                center_name,
                is_deserted,
                raw_row_text,
            ),
        )
        return int(cursor.fetchone()[0])

    def insert_bag_snapshot(
        self,
        *,
        publication_id: int,
        staff_group_id: int | None,
        bag_code: str,
        bag_name: str | None,
        source_kind: str,
        snapshot_date_text: str | None,
        snapshot_date_iso: str | None,
        zone_text: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO non_docent_bag_snapshots (
                publication_id,
                staff_group_id,
                bag_code,
                bag_name,
                source_kind,
                snapshot_date_text,
                snapshot_date_iso,
                zone_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                publication_id,
                staff_group_id,
                bag_code,
                bag_name,
                source_kind,
                snapshot_date_text,
                snapshot_date_iso,
                zone_text,
            ),
        )
        return int(cursor.fetchone()[0])

    def insert_bag_member(
        self,
        *,
        snapshot_id: int,
        order_number: int | None,
        masked_dni: str | None,
        person_display_name: str,
        person_name_normalized: str,
        total_score: float | None,
        status_text: str | None,
        annotation_text: str | None,
        start_date_text: str | None,
        end_date_text: str | None,
        merit_json: str | None,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO non_docent_bag_members (
                snapshot_id,
                order_number,
                masked_dni,
                person_display_name,
                person_name_normalized,
                total_score,
                status_text,
                annotation_text,
                start_date_text,
                end_date_text,
                merit_json,
                raw_row_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
            RETURNING id
            """,
            (
                snapshot_id,
                order_number,
                masked_dni,
                person_display_name,
                person_name_normalized,
                total_score,
                status_text,
                annotation_text,
                start_date_text,
                end_date_text,
                merit_json,
                raw_row_text,
            ),
        )
        return int(cursor.fetchone()[0])

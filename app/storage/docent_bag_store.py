from __future__ import annotations

from typing import Any

from app.storage.db import get_connection


DOCENT_BAG_DOC_FAMILIES = (
    "docent_bag_participants",
    "docent_career_practice_participants",
)


class DocentBagStore:
    def __init__(self, connection: Any | None = None) -> None:
        self.connection = connection or get_connection()

    def close(self) -> None:
        self.connection.close()

    def list_documents_for_parser(
        self,
        *,
        parser_key: str,
        parser_version: str,
    ) -> list[dict[str, Any]]:
        placeholders = ",".join("%s" for _ in DOCENT_BAG_DOC_FAMILIES)
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
                dv.file_path,
                dv.original_filename,
                dv.sha256,
                s.source_key,
                s.label AS source_label,
                s.source_url,
                a.url AS asset_url,
                a.canonical_url AS asset_canonical_url,
                a.publication_label,
                a.publication_date_text
            FROM documents d
            JOIN document_versions dv
                ON dv.id = d.document_version_id
            JOIN sources s
                ON s.id = d.source_id
            LEFT JOIN LATERAL (
                SELECT a2.url, a2.canonical_url, a2.publication_label, a2.publication_date_text
                FROM assets a2
                WHERE a2.document_version_id = d.document_version_id
                ORDER BY a2.id
                LIMIT 1
            ) a ON TRUE
            WHERE d.doc_family IN ({placeholders})
              AND NOT EXISTS (
                  SELECT 1
                  FROM document_parse_runs pr
                  WHERE pr.document_version_id = d.document_version_id
                    AND pr.parser_key = %s
                    AND pr.parser_version = %s
                    AND pr.status = 'success'
              )
            ORDER BY d.id
            """,
            (*DOCENT_BAG_DOC_FAMILIES, parser_key, parser_version),
        ).fetchall()
        return [dict(row) for row in rows]

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
            VALUES (%s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                document_version_id,
                parser_key,
                parser_version,
                "running",
                started_at,
            ),
        )
        return int(cursor.fetchone()[0])

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
                finished_at = %s,
                status = %s,
                rows_extracted = %s,
                error_message = %s
            WHERE id = %s
            """,
            (
                finished_at,
                status,
                rows_extracted,
                error_message,
                parse_run_id,
            ),
        )

    def clear_for_document(self, document_id: int) -> None:
        self.connection.execute(
            """
            DELETE FROM docent_bag_members
            WHERE snapshot_id IN (
                SELECT id
                FROM docent_bag_snapshots
                WHERE document_id = %s
            )
            """,
            (document_id,),
        )
        self.connection.execute(
            """
            DELETE FROM docent_bag_snapshots
            WHERE document_id = %s
            """,
            (document_id,),
        )

    def insert_snapshot(
        self,
        *,
        document_id: int,
        course_year: str | None,
        list_stage: str,
        staff_kind: str,
        position_scope: str,
        list_scope: str,
        body_code: str | None,
        body_name: str | None,
        specialty_code: str | None,
        specialty_name: str | None,
        source_page_url: str | None,
        document_url: str | None,
        notes: str | None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO docent_bag_snapshots (
                document_id,
                course_year,
                list_stage,
                staff_kind,
                position_scope,
                list_scope,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                source_page_url,
                document_url,
                notes
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                document_id,
                course_year,
                list_stage,
                staff_kind,
                position_scope,
                list_scope,
                body_code,
                body_name,
                specialty_code,
                specialty_name,
                source_page_url,
                document_url,
                notes,
            ),
        )
        return int(cursor.fetchone()[0])

    def insert_member(
        self,
        *,
        snapshot_id: int,
        order_number: int,
        masked_dni: str | None,
        person_display_name: str,
        person_name_normalized: str,
        service_status: str | None,
        collective: str | None,
        habilitations: list[str] | None,
        disabled_habilitation: bool,
        raw_row_text: str,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO docent_bag_members (
                snapshot_id,
                order_number,
                masked_dni,
                person_display_name,
                person_name_normalized,
                service_status,
                collective,
                habilitations,
                disabled_habilitation,
                raw_row_text
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                snapshot_id,
                order_number,
                masked_dni,
                person_display_name,
                person_name_normalized,
                service_status,
                collective,
                habilitations,
                disabled_habilitation,
                raw_row_text,
            ),
        )
        return int(cursor.fetchone()[0])

    def mark_document_parsed(self, document_id: int, parsed_at: str) -> None:
        self.connection.execute(
            """
            UPDATE documents
            SET parsed_at = %s
            WHERE id = %s
            """,
            (parsed_at, document_id),
        )

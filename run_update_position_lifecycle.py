from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.storage.db import get_connection


@dataclass(slots=True)
class LifecycleSummary:
    docent_offered_awarded: int = 0
    docent_offered_available: int = 0
    difficult_coverage_awarded: int = 0
    difficult_coverage_available: int = 0
    non_docent_adc_calls_closed: int = 0
    non_docent_adc_calls_not_visible: int = 0
    non_docent_positions_awarded: int = 0
    non_docent_positions_not_visible: int = 0
    non_docent_positions_available: int = 0


def _scalar(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return 0
    return int(row[0] or 0)


def update_docent_offered_positions(conn: Any) -> tuple[int, int]:
    conn.execute(
        """
        UPDATE offered_positions
        SET
            availability_status = 'available',
            availability_reason = NULL,
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        WHERE NOT EXISTS (
            SELECT 1
            FROM award_assignments aa
            WHERE aa.matched_offered_position_id = offered_positions.id
        )
        """
    )
    available = conn.execute("SELECT ROW_COUNT() AS count").fetchone()[0] if False else 0

    conn.execute(
        """
        UPDATE offered_positions op
        SET
            availability_status = 'awarded',
            availability_reason = 'matched_award_assignment',
            availability_checked_at = NOW(),
            closed_by_document_id = ar.document_id,
            closed_at = NOW()
        FROM award_assignments aa
        JOIN award_results ar ON ar.id = aa.award_result_id
        WHERE aa.matched_offered_position_id = op.id
        """
    )

    awarded = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM offered_positions
        WHERE availability_status = 'awarded'
        """,
    )
    available = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM offered_positions
        WHERE availability_status = 'available'
        """,
    )
    return awarded, available


def update_difficult_coverage_positions(conn: Any) -> tuple[int, int]:
    conn.execute(
        """
        UPDATE difficult_coverage_positions
        SET
            availability_status = 'available',
            availability_reason = NULL,
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        WHERE NOT EXISTS (
            SELECT 1
            FROM difficult_coverage_candidates dc
            WHERE dc.position_id = difficult_coverage_positions.id
              AND dc.is_selected IS TRUE
        )
        """
    )

    conn.execute(
        """
        UPDATE difficult_coverage_positions p
        SET
            availability_status = 'awarded',
            availability_reason = 'selected_difficult_coverage_candidate',
            availability_checked_at = NOW(),
            closed_by_document_id = p.document_id,
            closed_at = NOW()
        WHERE EXISTS (
            SELECT 1
            FROM difficult_coverage_candidates dc
            WHERE dc.position_id = p.id
              AND dc.is_selected IS TRUE
        )
        """
    )

    awarded = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM difficult_coverage_positions
        WHERE availability_status = 'awarded'
        """,
    )
    available = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM difficult_coverage_positions
        WHERE availability_status = 'available'
        """,
    )
    return awarded, available


def update_non_docent_adc_positions(conn: Any) -> tuple[int, int, int, int, int]:
    # Mark currently visible ADC calls from the latest successful scrape of each
    # non-docent ADC source. This prevents old calls kept in the database from
    # being shown as available once the official page no longer lists them.
    conn.execute(
        """
        WITH latest_successful_runs AS (
            SELECT DISTINCT ON (sr.source_id)
                sr.id AS sync_run_id,
                sr.source_id
            FROM sync_runs sr
            WHERE sr.status = 'success'
            ORDER BY sr.source_id, sr.finished_at DESC NULLS LAST, sr.id DESC
        ),
        visible_call_assets AS (
            SELECT
                s.source_key,
                regexp_replace(COALESCE(a.title, ''), '[^0-9]', '', 'g') AS code_digits
            FROM latest_successful_runs lr
            JOIN sources s ON s.id = lr.source_id
            JOIN assets a ON a.sync_run_id = lr.sync_run_id
            WHERE LEFT(s.source_key, 15) = 'non_docent_adc_'
              AND a.asset_role = 'non_docent_adc_call_pdf'
        ),
        visible_calls AS (
            SELECT p.id AS publication_id
            FROM non_docent_publications p
            JOIN documents d ON d.id = p.document_id
            JOIN sources s ON s.id = d.source_id
            JOIN visible_call_assets v
              ON v.source_key = s.source_key
             AND v.code_digits = regexp_replace(COALESCE(p.publication_code, ''), '[^0-9]', '', 'g')
            WHERE p.publication_kind = 'adc_call'
              AND regexp_replace(COALESCE(p.publication_code, ''), '[^0-9]', '', 'g') <> ''
              AND v.code_digits <> ''
        )
        UPDATE non_docent_publications p
        SET
            is_current = TRUE,
            availability_status = 'available',
            availability_reason = NULL,
            last_seen_at = NOW(),
            missing_since = NULL,
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        FROM visible_calls vc
        WHERE p.id = vc.publication_id
        """
    )

    conn.execute(
        """
        WITH latest_successful_runs AS (
            SELECT DISTINCT ON (sr.source_id)
                sr.id AS sync_run_id,
                sr.source_id
            FROM sync_runs sr
            WHERE sr.status = 'success'
            ORDER BY sr.source_id, sr.finished_at DESC NULLS LAST, sr.id DESC
        ),
        visible_call_assets AS (
            SELECT
                s.source_key,
                regexp_replace(COALESCE(a.title, ''), '[^0-9]', '', 'g') AS code_digits
            FROM latest_successful_runs lr
            JOIN sources s ON s.id = lr.source_id
            JOIN assets a ON a.sync_run_id = lr.sync_run_id
            WHERE LEFT(s.source_key, 15) = 'non_docent_adc_'
              AND a.asset_role = 'non_docent_adc_call_pdf'
        ),
        visible_calls AS (
            SELECT p.id AS publication_id
            FROM non_docent_publications p
            JOIN documents d ON d.id = p.document_id
            JOIN sources s ON s.id = d.source_id
            JOIN visible_call_assets v
              ON v.source_key = s.source_key
             AND v.code_digits = regexp_replace(COALESCE(p.publication_code, ''), '[^0-9]', '', 'g')
            WHERE p.publication_kind = 'adc_call'
              AND regexp_replace(COALESCE(p.publication_code, ''), '[^0-9]', '', 'g') <> ''
              AND v.code_digits <> ''
        ),
        sources_with_successful_run AS (
            SELECT DISTINCT s.source_key
            FROM latest_successful_runs lr
            JOIN sources s ON s.id = lr.source_id
            WHERE LEFT(s.source_key, 15) = 'non_docent_adc_'
        )
        UPDATE non_docent_publications p
        SET
            is_current = FALSE,
            availability_status = 'not_visible',
            availability_reason = 'adc_call_missing_from_latest_source_sync',
            missing_since = COALESCE(p.missing_since, NOW()),
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        FROM documents d
        JOIN sources s ON s.id = d.source_id
        WHERE p.document_id = d.id
          AND p.publication_kind = 'adc_call'
          AND EXISTS (
              SELECT 1
              FROM sources_with_successful_run swsr
              WHERE swsr.source_key = s.source_key
          )
          AND NOT EXISTS (
              SELECT 1
              FROM visible_calls vc
              WHERE vc.publication_id = p.id
          )
        """
    )

    conn.execute(
        """
        UPDATE non_docent_offered_positions pos
        SET
            availability_status = 'available',
            availability_reason = NULL,
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        FROM non_docent_publications p
        WHERE p.id = pos.publication_id
          AND p.publication_kind = 'adc_call'
          AND p.is_current IS TRUE
          AND COALESCE(p.availability_status, 'available') = 'available'
        """
    )

    conn.execute(
        """
        UPDATE non_docent_offered_positions pos
        SET
            availability_status = 'not_visible',
            availability_reason = 'adc_call_missing_from_latest_source_sync',
            availability_checked_at = NOW(),
            closed_by_document_id = NULL,
            closed_at = NULL
        FROM non_docent_publications p
        WHERE p.id = pos.publication_id
          AND p.publication_kind = 'adc_call'
          AND p.is_current IS FALSE
          AND p.availability_status = 'not_visible'
        """
    )

    # If an ADC award with the same staff group and normalized ADC code exists,
    # close the call and mark its offered positions as awarded. This overrides
    # not_visible because adjudication is more informative than disappearance.
    conn.execute(
        """
        WITH awarded_calls AS (
            SELECT
                call.id AS call_publication_id,
                MIN(award.document_id) AS award_document_id
            FROM non_docent_publications call
            JOIN non_docent_publications award
              ON award.publication_kind = 'adc_award'
             AND award.staff_group_id IS NOT DISTINCT FROM call.staff_group_id
             AND regexp_replace(COALESCE(award.publication_code, ''), '[^0-9/]', '', 'g') =
                 regexp_replace(COALESCE(call.publication_code, ''), '[^0-9/]', '', 'g')
            WHERE call.publication_kind = 'adc_call'
              AND regexp_replace(COALESCE(call.publication_code, ''), '[^0-9/]', '', 'g') <> ''
            GROUP BY call.id
        )
        UPDATE non_docent_publications p
        SET
            is_current = FALSE,
            availability_status = 'awarded',
            availability_reason = 'matching_adc_award_published',
            missing_since = COALESCE(p.missing_since, NOW()),
            availability_checked_at = NOW(),
            closed_by_document_id = ac.award_document_id,
            closed_at = NOW()
        FROM awarded_calls ac
        WHERE p.id = ac.call_publication_id
        """
    )

    conn.execute(
        """
        WITH awarded_calls AS (
            SELECT
                call.id AS call_publication_id,
                MIN(award.document_id) AS award_document_id
            FROM non_docent_publications call
            JOIN non_docent_publications award
              ON award.publication_kind = 'adc_award'
             AND award.staff_group_id IS NOT DISTINCT FROM call.staff_group_id
             AND regexp_replace(COALESCE(award.publication_code, ''), '[^0-9/]', '', 'g') =
                 regexp_replace(COALESCE(call.publication_code, ''), '[^0-9/]', '', 'g')
            WHERE call.publication_kind = 'adc_call'
              AND regexp_replace(COALESCE(call.publication_code, ''), '[^0-9/]', '', 'g') <> ''
            GROUP BY call.id
        )
        UPDATE non_docent_offered_positions pos
        SET
            availability_status = 'awarded',
            availability_reason = 'matching_adc_award_published',
            availability_checked_at = NOW(),
            closed_by_document_id = ac.award_document_id,
            closed_at = NOW()
        FROM awarded_calls ac
        WHERE pos.publication_id = ac.call_publication_id
        """
    )

    closed_calls = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM non_docent_publications
        WHERE publication_kind = 'adc_call'
          AND availability_status = 'awarded'
        """,
    )
    not_visible_calls = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM non_docent_publications
        WHERE publication_kind = 'adc_call'
          AND availability_status = 'not_visible'
        """,
    )
    positions_awarded = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM non_docent_offered_positions
        WHERE availability_status = 'awarded'
        """,
    )
    positions_not_visible = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM non_docent_offered_positions
        WHERE availability_status = 'not_visible'
        """,
    )
    positions_available = _scalar(
        conn,
        """
        SELECT COUNT(*)
        FROM non_docent_offered_positions
        WHERE availability_status = 'available'
        """,
    )
    return closed_calls, not_visible_calls, positions_awarded, positions_not_visible, positions_available

def run() -> LifecycleSummary:
    summary = LifecycleSummary()
    conn = get_connection()
    try:
        summary.docent_offered_awarded, summary.docent_offered_available = update_docent_offered_positions(conn)
        (
            summary.difficult_coverage_awarded,
            summary.difficult_coverage_available,
        ) = update_difficult_coverage_positions(conn)
        (
            summary.non_docent_adc_calls_closed,
            summary.non_docent_adc_calls_not_visible,
            summary.non_docent_positions_awarded,
            summary.non_docent_positions_not_visible,
            summary.non_docent_positions_available,
        ) = update_non_docent_adc_positions(conn)
        conn.commit()
        return summary
    except Exception:
        conn.rollback()
        raise
    finally:
        conn._conn.close()


if __name__ == "__main__":
    result = run()
    print("=" * 100)
    print("position_lifecycle")
    print(f"Docentes ofertadas adjudicadas: {result.docent_offered_awarded}")
    print(f"Docentes ofertadas disponibles: {result.docent_offered_available}")
    print(f"Difícil cobertura adjudicadas: {result.difficult_coverage_awarded}")
    print(f"Difícil cobertura disponibles: {result.difficult_coverage_available}")
    print(f"No docentes ADC convocatorias cerradas por adjudicación: {result.non_docent_adc_calls_closed}")
    print(f"No docentes ADC convocatorias no visibles: {result.non_docent_adc_calls_not_visible}")
    print(f"No docentes plazas adjudicadas: {result.non_docent_positions_awarded}")
    print(f"No docentes plazas no visibles: {result.non_docent_positions_not_visible}")
    print(f"No docentes plazas disponibles: {result.non_docent_positions_available}")

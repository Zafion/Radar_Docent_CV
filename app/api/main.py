from __future__ import annotations

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from app.web.routes import router as web_router

import os
import sqlite3
import unicodedata
from contextlib import contextmanager
from typing import Any, Iterable

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

DB_PATH = os.getenv("RADAR_DOCENT_DB_PATH", "/mnt/data/radar_docent_cv.db")


app = FastAPI(
    title="Radar Docent CV API",
    version="0.1.0",
    description="API de solo lectura sobre SQLite para consultar adjudicaciones y puestos docentes.",
)

BASE_DIR = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")
app.include_router(web_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@contextmanager
def get_connection() -> Iterable[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    yield conn
    conn.close()


# Registrar función SQLite para búsquedas tolerantes a acentos.
def _register_normalize_function(conn: sqlite3.Connection) -> None:
    def normalize_text(value: str | None) -> str:
        if not value:
            return ""
        value = value.strip().lower()
        value = unicodedata.normalize("NFKD", value)
        return "".join(ch for ch in value if not unicodedata.combining(ch))

    conn.create_function("normalize_text", 1, normalize_text)


with sqlite3.connect(DB_PATH) as _bootstrap_conn:
    _register_normalize_function(_bootstrap_conn)


@app.middleware("http")
async def sqlite_function_middleware(request, call_next):
    # No muta la request; se limita a validar que la BD existe.
    if not os.path.exists(DB_PATH):
        raise HTTPException(status_code=500, detail=f"SQLite no encontrada en {DB_PATH}")
    return await call_next(request)


# ---------- helpers ----------

def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


ALLOWED_AWARD_ORDER_FIELDS = {
    "document_date": "d.document_date_iso",
    "person_name": "ar.person_display_name",
    "status": "ar.status",
    "order_number": "ar.order_number",
    "id": "ar.id",
}

ALLOWED_OFFERED_ORDER_FIELDS = {
    "document_date": "d.document_date_iso",
    "locality": "op.locality",
    "center_name": "op.center_name",
    "position_code": "op.position_code",
    "id": "op.id",
}

ALLOWED_DIFFICULT_ORDER_FIELDS = {
    "document_date": "d.document_date_iso",
    "locality": "p.locality",
    "center_name": "p.center_name",
    "position_code": "p.position_code",
    "candidate_count": "candidate_count",
    "id": "p.id",
}


def build_order_by(field: str, direction: str, allowed: dict[str, str], fallback: str) -> str:
    sql_field = allowed.get(field, fallback)
    sql_dir = "DESC" if direction.lower() == "desc" else "ASC"
    return f" ORDER BY {sql_field} {sql_dir} "


def make_label(code: str | None, name: str | None) -> str | None:
    if code and name:
        return f"{code} - {name}"
    return code or name


def map_list_scope_label(list_scope: str | None) -> str | None:
    mapping = {
        "maestros": "Maestros",
        "secundaria_otros": "Secundaria y otros cuerpos",
        "dificil_cobertura": "Difícil cobertura",
        "continua": "Adjudicación continua",
    }
    if not list_scope:
        return None
    return mapping.get(list_scope, list_scope.replace("_", " ").capitalize())


def build_user_view(
    person: dict[str, Any],
    awards: list[dict[str, Any]],
    difficult_coverage: list[dict[str, Any]],
) -> dict[str, Any]:
    latest_award = awards[0] if awards else None

    latest_awarded = next(
        (
            award
            for award in awards
            if award.get("status") == "Adjudicat" and award.get("assignments_count", 0) > 0
        ),
        None,
    )

    selected_difficult = next(
        (row for row in difficult_coverage if row.get("is_selected") == 1),
        None,
    )

    base = {
        "display_name": person["display_name"],
        "current_result": "no_data",
        "current_result_label": "Sin datos interpretables",
        "current_result_message": "No hay información suficiente para mostrar un resumen claro.",
        "latest_scope_label": None,
        "latest_specialty_label": None,
        "latest_date": None,
        "assigned_position": None,
        "assigned_center": None,
        "assigned_locality": None,
        "recommended_action": None,
    }

    if latest_awarded:
        first_assignment = latest_awarded["assignments"][0] if latest_awarded["assignments"] else None
        return {
            **base,
            "current_result": "awarded",
            "current_result_label": "Adjudicado",
            "current_result_message": "Sí tienes una plaza adjudicada en los datos cargados.",
            "latest_scope_label": map_list_scope_label(latest_awarded.get("list_scope")),
            "latest_specialty_label": make_label(
                latest_awarded.get("specialty_code"),
                latest_awarded.get("specialty_name"),
            ),
            "latest_date": latest_awarded.get("document_date_iso"),
            "assigned_position": first_assignment.get("position_code") if first_assignment else None,
            "assigned_center": first_assignment.get("center_name") if first_assignment else None,
            "assigned_locality": first_assignment.get("locality") if first_assignment else None,
            "recommended_action": "Consulta la resolución oficial y el centro adjudicado para los siguientes pasos administrativos.",
        }

    if latest_award:
        status = latest_award.get("status")
        scope_label = map_list_scope_label(latest_award.get("list_scope"))
        specialty_label = make_label(
            latest_award.get("specialty_code"),
            latest_award.get("specialty_name"),
        )

        if status == "No adjudicat":
            return {
                **base,
                "current_result": "not_awarded",
                "current_result_label": "No adjudicado",
                "current_result_message": "No te han adjudicado plaza en los datos cargados.",
                "latest_scope_label": scope_label,
                "latest_specialty_label": specialty_label,
                "latest_date": latest_award.get("document_date_iso"),
                "recommended_action": "No consta una plaza adjudicada en los datos cargados. Puedes revisar futuras adjudicaciones o procedimientos posteriores.",
            }

        if status == "Ha participat":
            return {
                **base,
                "current_result": "participated_without_award",
                "current_result_label": "Ha participado",
                "current_result_message": "Has participado, pero en este registro no consta una plaza adjudicada.",
                "latest_scope_label": scope_label,
                "latest_specialty_label": specialty_label,
                "latest_date": latest_award.get("document_date_iso"),
                "recommended_action": "Revisa publicaciones posteriores por si aparece una nueva adjudicación.",
            }

        if status == "No ha participat":
            return {
                **base,
                "current_result": "not_participated",
                "current_result_label": "No ha participado",
                "current_result_message": "No has participado en este procedimiento.",
                "latest_scope_label": scope_label,
                "latest_specialty_label": specialty_label,
                "latest_date": latest_award.get("document_date_iso"),
                "recommended_action": "Comprueba que estás consultando el procedimiento correcto y revisa futuras convocatorias.",
            }

        if status == "Desactivat":
            return {
                **base,
                "current_result": "deactivated",
                "current_result_label": "Desactivado",
                "current_result_message": "Figuras como desactivado en este procedimiento.",
                "latest_scope_label": scope_label,
                "latest_specialty_label": specialty_label,
                "latest_date": latest_award.get("document_date_iso"),
                "recommended_action": "Consulta la información oficial del procedimiento para verificar tu situación administrativa.",
            }

    if selected_difficult:
        return {
            **base,
            "current_result": "selected_difficult_coverage",
            "current_result_label": "Seleccionado en difícil cobertura",
            "current_result_message": "Consta como seleccionado en un procedimiento de difícil cobertura.",
            "latest_scope_label": "Difícil cobertura",
            "latest_specialty_label": make_label(
                selected_difficult.get("specialty_code"),
                selected_difficult.get("specialty_name"),
            ),
            "latest_date": selected_difficult.get("document_date_iso"),
            "assigned_position": selected_difficult.get("assigned_position_code") or selected_difficult.get("position_code"),
            "assigned_center": selected_difficult.get("center_name"),
            "assigned_locality": selected_difficult.get("locality"),
            "recommended_action": "Consulta la publicación oficial para confirmar el resultado y los pasos administrativos siguientes.",
        }

    if difficult_coverage:
        latest_difficult = difficult_coverage[0]
        return {
            **base,
            "current_result": "difficult_coverage_candidate",
            "current_result_label": "Participante en difícil cobertura",
            "current_result_message": "Constas como participante en un procedimiento de difícil cobertura, sin selección reflejada en los datos cargados.",
            "latest_scope_label": "Difícil cobertura",
            "latest_specialty_label": make_label(
                latest_difficult.get("specialty_code"),
                latest_difficult.get("specialty_name"),
            ),
            "latest_date": latest_difficult.get("document_date_iso"),
            "recommended_action": "Revisa la publicación oficial por si hay nuevas actualizaciones o resultados posteriores.",
        }

    return base


# ---------- health ----------

@app.get("/health")
def health() -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        counts = {
            "documents": conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0],
            "offered_positions": conn.execute("SELECT COUNT(*) FROM offered_positions").fetchone()[0],
            "award_results": conn.execute("SELECT COUNT(*) FROM award_results").fetchone()[0],
            "award_assignments": conn.execute("SELECT COUNT(*) FROM award_assignments").fetchone()[0],
            "difficult_coverage_positions": conn.execute(
                "SELECT COUNT(*) FROM difficult_coverage_positions"
            ).fetchone()[0],
            "difficult_coverage_candidates": conn.execute(
                "SELECT COUNT(*) FROM difficult_coverage_candidates"
            ).fetchone()[0],
        }
        latest_docs = rows_to_dicts(
            conn.execute(
                """
                SELECT d.id, d.doc_family, d.title, d.document_date_iso, d.list_scope, s.source_key
                FROM documents d
                JOIN sources s ON s.id = d.source_id
                ORDER BY COALESCE(d.document_date_iso, '') DESC, d.id DESC
                LIMIT 5
                """
            ).fetchall()
        )
    return {
        "status": "ok",
        "db_path": DB_PATH,
        "counts": counts,
        "latest_documents": latest_docs,
    }


# ---------- persons search ----------

@app.get("/api/search/persons")
def search_persons(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    pattern = f"%{q}%"
    with get_connection() as conn:
        _register_normalize_function(conn)
        sql = """
        WITH award_people AS (
            SELECT
                ar.person_name_normalized AS normalized_name,
                ar.person_display_name AS display_name,
                'award_results' AS source_kind,
                COUNT(*) AS total_records,
                SUM(CASE WHEN ar.status = 'Adjudicat' THEN 1 ELSE 0 END) AS total_awarded,
                NULL AS total_difficult_positions
            FROM award_results ar
            WHERE normalize_text(ar.person_display_name) LIKE normalize_text(?)
               OR ar.person_name_normalized LIKE normalize_text(?)
            GROUP BY ar.person_name_normalized, ar.person_display_name
        ),
        difficult_people AS (
            SELECT
                dc.full_name_normalized AS normalized_name,
                dc.full_name AS display_name,
                'difficult_coverage_candidates' AS source_kind,
                COUNT(*) AS total_records,
                SUM(CASE WHEN dc.is_selected = 1 THEN 1 ELSE 0 END) AS total_awarded,
                COUNT(DISTINCT dc.position_id) AS total_difficult_positions
            FROM difficult_coverage_candidates dc
            WHERE normalize_text(dc.full_name) LIKE normalize_text(?)
               OR dc.full_name_normalized LIKE normalize_text(?)
            GROUP BY dc.full_name_normalized, dc.full_name
        ),
        unioned AS (
            SELECT * FROM award_people
            UNION ALL
            SELECT * FROM difficult_people
        )
        SELECT
            normalized_name,
            display_name,
            SUM(total_records) AS total_records,
            SUM(total_awarded) AS total_awarded,
            SUM(COALESCE(total_difficult_positions, 0)) AS total_difficult_positions,
            GROUP_CONCAT(source_kind, ',') AS source_kinds
        FROM unioned
        GROUP BY normalized_name, display_name
        ORDER BY total_records DESC, display_name ASC
        LIMIT ?
        """
        items = rows_to_dicts(conn.execute(sql, [pattern, pattern, pattern, pattern, limit]).fetchall())
    return {"items": items, "count": len(items), "query": q}

@app.get("/api/persons/profile")
def get_person_profile(
    normalized_name: str = Query(
        ...,
        min_length=2,
        description="Valor exacto devuelto por /api/search/persons -> normalized_name",
    ),
) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)

        person = conn.execute(
            """
            WITH candidates AS (
                SELECT
                    ar.person_name_normalized AS normalized_name,
                    ar.person_display_name AS display_name,
                    COUNT(*) AS total_rows
                FROM award_results ar
                WHERE ar.person_name_normalized = ?
                GROUP BY ar.person_name_normalized, ar.person_display_name

                UNION ALL

                SELECT
                    dc.full_name_normalized AS normalized_name,
                    dc.full_name AS display_name,
                    COUNT(*) AS total_rows
                FROM difficult_coverage_candidates dc
                WHERE dc.full_name_normalized = ?
                GROUP BY dc.full_name_normalized, dc.full_name
            )
            SELECT
                normalized_name,
                display_name
            FROM candidates
            ORDER BY total_rows DESC, display_name ASC
            LIMIT 1
            """,
            [normalized_name, normalized_name],
        ).fetchone()

        if person is None:
            raise HTTPException(status_code=404, detail="normalized_name no encontrado")

        summary = conn.execute(
            """
            WITH award_summary AS (
                SELECT
                    COUNT(*) AS total_award_records,
                    SUM(CASE WHEN ar.status = 'Adjudicat' THEN 1 ELSE 0 END) AS total_awarded,
                    MAX(d.document_date_iso) AS last_award_date
                FROM award_results ar
                JOIN documents d ON d.id = ar.document_id
                WHERE ar.person_name_normalized = ?
            ),
            assignment_summary AS (
                SELECT
                    COUNT(*) AS total_assignments
                FROM award_assignments aa
                JOIN award_results ar ON ar.id = aa.award_result_id
                WHERE ar.person_name_normalized = ?
            ),
            difficult_summary AS (
                SELECT
                    COUNT(*) AS total_difficult_coverage_candidates,
                    SUM(CASE WHEN dc.is_selected = 1 THEN 1 ELSE 0 END) AS total_difficult_selected,
                    COUNT(DISTINCT dc.position_id) AS total_difficult_positions,
                    MAX(d.document_date_iso) AS last_difficult_date
                FROM difficult_coverage_candidates dc
                JOIN difficult_coverage_positions p ON p.id = dc.position_id
                JOIN documents d ON d.id = p.document_id
                WHERE dc.full_name_normalized = ?
            )
            SELECT
                COALESCE(award_summary.total_award_records, 0) AS total_award_records,
                COALESCE(award_summary.total_awarded, 0) AS total_awarded,
                COALESCE(assignment_summary.total_assignments, 0) AS total_assignments,
                COALESCE(difficult_summary.total_difficult_coverage_candidates, 0) AS total_difficult_coverage_candidates,
                COALESCE(difficult_summary.total_difficult_selected, 0) AS total_difficult_selected,
                COALESCE(difficult_summary.total_difficult_positions, 0) AS total_difficult_positions,
                CASE
                    WHEN award_summary.last_award_date IS NULL THEN difficult_summary.last_difficult_date
                    WHEN difficult_summary.last_difficult_date IS NULL THEN award_summary.last_award_date
                    WHEN award_summary.last_award_date >= difficult_summary.last_difficult_date THEN award_summary.last_award_date
                    ELSE difficult_summary.last_difficult_date
                END AS last_seen_date
            FROM award_summary, assignment_summary, difficult_summary
            """,
            [normalized_name, normalized_name, normalized_name],
        ).fetchone()

        awards = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    ar.id,
                    ar.document_id,
                    d.document_date_iso,
                    d.title AS document_title,
                    s.source_key,
                    ar.list_scope,
                    ar.body_code,
                    ar.body_name,
                    ar.specialty_code,
                    ar.specialty_name,
                    ar.order_number,
                    ar.person_display_name,
                    ar.status,
                    EXISTS (
                        SELECT 1
                        FROM award_assignments aa
                        WHERE aa.award_result_id = ar.id
                    ) AS has_assignment,
                    (
                        SELECT COUNT(*)
                        FROM award_assignments aa
                        WHERE aa.award_result_id = ar.id
                    ) AS assignments_count,
                    (
                        SELECT COUNT(*)
                        FROM award_assignments aa
                        WHERE aa.award_result_id = ar.id
                          AND aa.matched_offered_position_id IS NOT NULL
                    ) AS matched_assignments_count
                FROM award_results ar
                JOIN documents d ON d.id = ar.document_id
                JOIN sources s ON s.id = d.source_id
                WHERE ar.person_name_normalized = ?
                ORDER BY COALESCE(d.document_date_iso, '') DESC, ar.document_id DESC, ar.order_number ASC, ar.id ASC
                """,
                [normalized_name],
            ).fetchall()
        )

        award_ids = [award["id"] for award in awards]
        assignments_by_award: dict[int, list[dict[str, Any]]] = {}

        if award_ids:
            placeholders = ",".join("?" for _ in award_ids)
            assignment_rows = rows_to_dicts(
                conn.execute(
                    f"""
                    SELECT
                        aa.id,
                        aa.award_result_id,
                        aa.assignment_kind,
                        aa.locality,
                        aa.center_code,
                        aa.center_name,
                        aa.position_specialty_code,
                        aa.position_specialty_name,
                        aa.position_code,
                        aa.hours_text,
                        aa.hours_value,
                        aa.petition_text,
                        aa.petition_number,
                        aa.request_type,
                        aa.matched_offered_position_id,
                        op.source_type AS matched_source_type,
                        op.position_type AS matched_position_type,
                        op.province AS matched_province,
                        op.locality AS matched_locality,
                        op.center_name AS matched_center_name,
                        op.specialty_code AS matched_specialty_code,
                        op.specialty_name AS matched_specialty_name,
                        op.observations AS matched_observations
                    FROM award_assignments aa
                    LEFT JOIN offered_positions op ON op.id = aa.matched_offered_position_id
                    WHERE aa.award_result_id IN ({placeholders})
                    ORDER BY aa.award_result_id ASC, aa.id ASC
                    """,
                    award_ids,
                ).fetchall()
            )

            for row in assignment_rows:
                award_result_id = row["award_result_id"]
                assignments_by_award.setdefault(award_result_id, []).append(row)

        for award in awards:
            award["assignments"] = assignments_by_award.get(award["id"], [])

        difficult_coverage = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    dc.id,
                    dc.position_id,
                    d.document_date_iso,
                    d.title AS document_title,
                    s.source_key,
                    p.body_code,
                    p.body_name,
                    p.specialty_code,
                    p.specialty_name,
                    p.position_code,
                    p.center_code,
                    p.center_name,
                    p.locality,
                    dc.row_number,
                    dc.is_selected,
                    dc.registration_datetime_text,
                    dc.registration_code_or_bag_order,
                    dc.petition_text,
                    dc.petition_number,
                    dc.has_master_text,
                    dc.valenciano_requirement_text,
                    dc.adjudication_group_text,
                    dc.assigned_position_code
                FROM difficult_coverage_candidates dc
                JOIN difficult_coverage_positions p ON p.id = dc.position_id
                JOIN documents d ON d.id = p.document_id
                JOIN sources s ON s.id = d.source_id
                WHERE dc.full_name_normalized = ?
                ORDER BY COALESCE(d.document_date_iso, '') DESC, dc.is_selected DESC, dc.row_number ASC, dc.id ASC
                """,
                [normalized_name],
            ).fetchall()
        )

    user_view = build_user_view(dict(person), awards, difficult_coverage)

    return {
        "person": {
            "normalized_name": person["normalized_name"],
            "display_name": person["display_name"],
        },
        "user_view": user_view,
        "summary": dict(summary),
        "awards": awards,
        "difficult_coverage": difficult_coverage,
    }


# ---------- awards ----------

@app.get("/api/awards")
def list_awards(
    q: str | None = Query(None, description="Texto libre sobre nombre de persona"),
    list_scope: str | None = None,
    status: str | None = None,
    source_key: str | None = None,
    document_id: int | None = None,
    document_date: str | None = Query(None, description="YYYY-MM-DD"),
    has_assignment: bool | None = None,
    matched_only: bool | None = None,
    order_by: str = Query("document_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if q:
        where.append("(normalize_text(ar.person_display_name) LIKE normalize_text(?) OR ar.person_name_normalized LIKE normalize_text(?))")
        pattern = f"%{q}%"
        params.extend([pattern, pattern])
    if list_scope:
        where.append("ar.list_scope = ?")
        params.append(list_scope)
    if status:
        where.append("ar.status = ?")
        params.append(status)
    if source_key:
        where.append("s.source_key = ?")
        params.append(source_key)
    if document_id:
        where.append("d.id = ?")
        params.append(document_id)
    if document_date:
        where.append("d.document_date_iso = ?")
        params.append(document_date)
    if has_assignment is True:
        where.append("EXISTS (SELECT 1 FROM award_assignments aa2 WHERE aa2.award_result_id = ar.id)")
    elif has_assignment is False:
        where.append("NOT EXISTS (SELECT 1 FROM award_assignments aa2 WHERE aa2.award_result_id = ar.id)")
    if matched_only is True:
        where.append(
            "EXISTS (SELECT 1 FROM award_assignments aa3 WHERE aa3.award_result_id = ar.id AND aa3.matched_offered_position_id IS NOT NULL)"
        )
    elif matched_only is False:
        where.append(
            "NOT EXISTS (SELECT 1 FROM award_assignments aa3 WHERE aa3.award_result_id = ar.id AND aa3.matched_offered_position_id IS NOT NULL)"
        )

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(order_by, order_dir, ALLOWED_AWARD_ORDER_FIELDS, "d.document_date_iso")

    base_sql = f"""
        FROM award_results ar
        JOIN documents d ON d.id = ar.document_id
        JOIN sources s ON s.id = d.source_id
        {where_sql}
    """

    items_sql = (
        """
        SELECT
            ar.id,
            ar.document_id,
            d.document_date_iso,
            s.source_key,
            ar.list_scope,
            ar.body_code,
            ar.body_name,
            ar.specialty_code,
            ar.specialty_name,
            ar.order_number,
            ar.person_display_name,
            ar.status,
            EXISTS (SELECT 1 FROM award_assignments aa WHERE aa.award_result_id = ar.id) AS has_assignment,
            (
                SELECT COUNT(*)
                FROM award_assignments aa
                WHERE aa.award_result_id = ar.id
            ) AS assignments_count,
            (
                SELECT COUNT(*)
                FROM award_assignments aa
                WHERE aa.award_result_id = ar.id
                  AND aa.matched_offered_position_id IS NOT NULL
            ) AS matched_assignments_count
        """
        + base_sql
        + order_sql
        + " LIMIT ? OFFSET ? "
    )
    count_sql = "SELECT COUNT(*) " + base_sql

    with get_connection() as conn:
        _register_normalize_function(conn)
        total = conn.execute(count_sql, params).fetchone()[0]
        items = rows_to_dicts(conn.execute(items_sql, [*params, limit, offset]).fetchall())

    return {
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@app.get("/api/awards/{award_result_id}")
def get_award_detail(award_result_id: int) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        award = conn.execute(
            """
            SELECT
                ar.id,
                ar.document_id,
                d.document_date_iso,
                d.doc_family,
                d.title AS document_title,
                d.list_scope AS document_list_scope,
                s.source_key,
                ar.list_scope,
                ar.body_code,
                ar.body_name,
                ar.specialty_code,
                ar.specialty_name,
                ar.order_number,
                ar.person_display_name,
                ar.person_name_normalized,
                ar.status,
                ar.raw_block_text
            FROM award_results ar
            JOIN documents d ON d.id = ar.document_id
            JOIN sources s ON s.id = d.source_id
            WHERE ar.id = ?
            """,
            [award_result_id],
        ).fetchone()
        if award is None:
            raise HTTPException(status_code=404, detail="award_result_id no encontrado")

        assignments = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    aa.id,
                    aa.assignment_kind,
                    aa.locality,
                    aa.center_code,
                    aa.center_name,
                    aa.position_specialty_code,
                    aa.position_specialty_name,
                    aa.position_code,
                    aa.hours_text,
                    aa.hours_value,
                    aa.petition_text,
                    aa.petition_number,
                    aa.request_type,
                    aa.matched_offered_position_id,
                    aa.raw_assignment_text,
                    op.document_id AS matched_document_id,
                    op.source_type AS matched_source_type,
                    op.position_type AS matched_position_type,
                    op.province AS matched_province,
                    op.locality AS matched_locality,
                    op.center_name AS matched_center_name,
                    op.specialty_code AS matched_specialty_code,
                    op.specialty_name AS matched_specialty_name,
                    op.observations AS matched_observations
                FROM award_assignments aa
                LEFT JOIN offered_positions op ON op.id = aa.matched_offered_position_id
                WHERE aa.award_result_id = ?
                ORDER BY aa.id ASC
                """,
                [award_result_id],
            ).fetchall()
        )

    return {"award": dict(award), "assignments": assignments}


# ---------- offered positions ----------

@app.get("/api/offered-positions")
def list_offered_positions(
    source_type: str | None = None,
    source_key: str | None = None,
    document_id: int | None = None,
    document_date: str | None = None,
    body_code: str | None = None,
    specialty_code: str | None = None,
    province: str | None = None,
    locality: str | None = None,
    center_code: str | None = None,
    position_code: str | None = None,
    position_type: str | None = None,
    only_unmatched: bool | None = Query(None, description="True = puestos que no aparecen enlazados desde award_assignments"),
    order_by: str = Query("document_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if source_type:
        where.append("op.source_type = ?")
        params.append(source_type)
    if source_key:
        where.append("s.source_key = ?")
        params.append(source_key)
    if document_id:
        where.append("d.id = ?")
        params.append(document_id)
    if document_date:
        where.append("d.document_date_iso = ?")
        params.append(document_date)
    if body_code:
        where.append("op.body_code = ?")
        params.append(body_code)
    if specialty_code:
        where.append("op.specialty_code = ?")
        params.append(specialty_code)
    if province:
        where.append("op.province = ?")
        params.append(province)
    if locality:
        where.append("normalize_text(op.locality) LIKE normalize_text(?)")
        params.append(f"%{locality}%")
    if center_code:
        where.append("op.center_code = ?")
        params.append(center_code)
    if position_code:
        where.append("op.position_code = ?")
        params.append(position_code)
    if position_type:
        where.append("op.position_type = ?")
        params.append(position_type)
    if only_unmatched is True:
        where.append("NOT EXISTS (SELECT 1 FROM award_assignments aa WHERE aa.matched_offered_position_id = op.id)")
    elif only_unmatched is False:
        where.append("EXISTS (SELECT 1 FROM award_assignments aa WHERE aa.matched_offered_position_id = op.id)")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(order_by, order_dir, ALLOWED_OFFERED_ORDER_FIELDS, "d.document_date_iso")
    base_sql = f"""
        FROM offered_positions op
        JOIN documents d ON d.id = op.document_id
        JOIN sources s ON s.id = d.source_id
        {where_sql}
    """

    items_sql = (
        """
        SELECT
            op.id,
            op.document_id,
            d.document_date_iso,
            s.source_key,
            op.source_type,
            op.body_code,
            op.body_name,
            op.specialty_code,
            op.specialty_name,
            op.province,
            op.locality,
            op.center_code,
            op.center_name,
            op.position_code,
            op.hours_text,
            op.hours_value,
            op.is_itinerant,
            op.valenciano_required_text,
            op.position_type,
            op.composition,
            op.observations,
            (
                SELECT COUNT(*)
                FROM award_assignments aa
                WHERE aa.matched_offered_position_id = op.id
            ) AS matched_assignments_count
        """
        + base_sql
        + order_sql
        + " LIMIT ? OFFSET ? "
    )
    count_sql = "SELECT COUNT(*) " + base_sql

    with get_connection() as conn:
        _register_normalize_function(conn)
        total = conn.execute(count_sql, params).fetchone()[0]
        items = rows_to_dicts(conn.execute(items_sql, [*params, limit, offset]).fetchall())

    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------- difficult coverage ----------

@app.get("/api/difficult-coverage/positions")
def list_difficult_positions(
    document_id: int | None = None,
    document_date: str | None = None,
    body_code: str | None = None,
    specialty_code: str | None = None,
    locality: str | None = None,
    center_code: str | None = None,
    position_code: str | None = None,
    selected_only: bool | None = Query(None, description="True = solo puestos con al menos una persona seleccionada"),
    order_by: str = Query("document_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if document_id:
        where.append("d.id = ?")
        params.append(document_id)
    if document_date:
        where.append("d.document_date_iso = ?")
        params.append(document_date)
    if body_code:
        where.append("p.body_code = ?")
        params.append(body_code)
    if specialty_code:
        where.append("p.specialty_code = ?")
        params.append(specialty_code)
    if locality:
        where.append("normalize_text(p.locality) LIKE normalize_text(?)")
        params.append(f"%{locality}%")
    if center_code:
        where.append("p.center_code = ?")
        params.append(center_code)
    if position_code:
        where.append("p.position_code = ?")
        params.append(position_code)
    if selected_only is True:
        where.append("EXISTS (SELECT 1 FROM difficult_coverage_candidates dc WHERE dc.position_id = p.id AND dc.is_selected = 1)")
    elif selected_only is False:
        where.append("NOT EXISTS (SELECT 1 FROM difficult_coverage_candidates dc WHERE dc.position_id = p.id AND dc.is_selected = 1)")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(order_by, order_dir, ALLOWED_DIFFICULT_ORDER_FIELDS, "d.document_date_iso")

    base_sql = f"""
        FROM difficult_coverage_positions p
        JOIN documents d ON d.id = p.document_id
        JOIN sources s ON s.id = d.source_id
        {where_sql}
    """

    items_sql = (
        """
        SELECT
            p.id,
            p.document_id,
            d.document_date_iso,
            s.source_key,
            p.body_code,
            p.body_name,
            p.specialty_code,
            p.specialty_name,
            p.position_code,
            p.center_code,
            p.center_name,
            p.locality,
            p.num_participants,
            p.sorteo_number,
            p.registro_superior,
            p.registro_inferior,
            (
                SELECT COUNT(*)
                FROM difficult_coverage_candidates dc
                WHERE dc.position_id = p.id
            ) AS candidate_count,
            (
                SELECT COUNT(*)
                FROM difficult_coverage_candidates dc
                WHERE dc.position_id = p.id AND dc.is_selected = 1
            ) AS selected_candidate_count
        """
        + base_sql
        + order_sql
        + " LIMIT ? OFFSET ? "
    )
    count_sql = "SELECT COUNT(*) " + base_sql

    with get_connection() as conn:
        _register_normalize_function(conn)
        total = conn.execute(count_sql, params).fetchone()[0]
        items = rows_to_dicts(conn.execute(items_sql, [*params, limit, offset]).fetchall())

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/difficult-coverage/positions/{position_id}/candidates")
def get_difficult_candidates(
    position_id: int,
    selected_only: bool | None = None,
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        position = conn.execute(
            """
            SELECT
                p.id,
                p.document_id,
                d.document_date_iso,
                s.source_key,
                p.body_code,
                p.body_name,
                p.specialty_code,
                p.specialty_name,
                p.position_code,
                p.center_code,
                p.center_name,
                p.locality,
                p.num_participants,
                p.sorteo_number,
                p.registro_superior,
                p.registro_inferior
            FROM difficult_coverage_positions p
            JOIN documents d ON d.id = p.document_id
            JOIN sources s ON s.id = d.source_id
            WHERE p.id = ?
            """,
            [position_id],
        ).fetchone()
        if position is None:
            raise HTTPException(status_code=404, detail="position_id no encontrado")

        where = ["dc.position_id = ?"]
        params: list[Any] = [position_id]
        if selected_only is True:
            where.append("dc.is_selected = 1")
        elif selected_only is False:
            where.append("dc.is_selected = 0")
        where_sql = f"WHERE {' AND '.join(where)}"

        count_sql = f"SELECT COUNT(*) FROM difficult_coverage_candidates dc {where_sql}"
        items_sql = f"""
            SELECT
                dc.id,
                dc.row_number,
                dc.is_selected,
                dc.last_name_1,
                dc.last_name_2,
                dc.first_name,
                dc.full_name,
                dc.full_name_normalized,
                dc.registration_datetime_text,
                dc.registration_code_or_bag_order,
                dc.petition_text,
                dc.petition_number,
                dc.has_master_text,
                dc.valenciano_requirement_text,
                dc.adjudication_group_text,
                dc.assigned_position_code,
                dc.raw_row_text
            FROM difficult_coverage_candidates dc
            {where_sql}
            ORDER BY dc.is_selected DESC, dc.row_number ASC, dc.id ASC
            LIMIT ? OFFSET ?
        """
        total = conn.execute(count_sql, params).fetchone()[0]
        items = rows_to_dicts(conn.execute(items_sql, [*params, limit, offset]).fetchall())

    return {
        "position": dict(position),
        "items": items,
        "total": total,
        "limit": limit,
        "offset": offset,
    }

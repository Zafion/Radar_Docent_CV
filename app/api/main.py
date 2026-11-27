from __future__ import annotations

from pathlib import Path
from fastapi.staticfiles import StaticFiles
from app.web.routes import TEMPLATES, router as web_router
from app.web.i18n import get_language_from_path, translate_html

import os
import re
from datetime import date
from contextlib import contextmanager
from typing import Any, Iterable, Sequence

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, PlainTextResponse, Response
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.services.geo import (
    haversine_km,
    build_google_maps_search_url,
    build_google_maps_directions_url,
)
from app.services.push_notifications import (
    get_vapid_public_key,
    is_push_configured,
)
from app.storage.db import get_pool, close_pool
from app.storage.push_subscription_store import (
    upsert_push_subscription,
    deactivate_push_subscription,
)
from app.storage.public_alert_store import count_public_alerts, list_public_alerts
from app.api.rate_limit import ApiRateLimitMiddleware


DB_URL = os.getenv("RADAR_DOCENT_DB_URL", "").strip()
DB_PATH = "postgresql"
RECOVERY_READ_ONLY = os.getenv("FUNK_RECOVERY_READ_ONLY", "0") == "1"


app = FastAPI(
    title="Radar Docent CV API",
    version="0.1.0",
    description="API de solo lectura sobre PostgreSQL para consultar adjudicaciones y puestos docentes.",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

BASE_DIR = Path(__file__).resolve().parents[1]
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "web" / "static")), name="static")
app.include_router(web_router)


@app.on_event("startup")
def startup_event() -> None:
    if not DB_URL:
        raise RuntimeError("RADAR_DOCENT_DB_URL is not configured")
    if RECOVERY_READ_ONLY and os.getenv('ENABLE_NOTIFICATIONS', '').strip() != '0':
        raise RuntimeError('recovery_notifications_must_be_disabled')
    pool = get_pool()
    if RECOVERY_READ_ONLY:
        with pool.connection() as conn:
            verify_recovery_database_role(conn)


def verify_recovery_database_role(conn) -> None:
    """Fail closed: the application DB identity must itself lack write authority."""
    role = conn.execute("SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()
    if not role or any(role.values()):
        raise RuntimeError('recovery_role_has_elevated_privileges')
    elevated = conn.execute("SELECT EXISTS (SELECT 1 FROM pg_roles r WHERE (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls) AND pg_has_role(current_user,r.oid,'MEMBER')) AS unsafe").fetchone()
    if elevated['unsafe']:
        raise RuntimeError('recovery_role_has_elevated_membership')
    if conn.execute("SELECT current_setting('transaction_read_only') AS value").fetchone()['value'] != 'on':
        raise RuntimeError('recovery_transaction_not_read_only')
    unsafe = conn.execute("""SELECT has_database_privilege(current_user,current_database(),'TEMP') OR EXISTS (
      SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
        AND c.relkind IN ('r','p','v','m','f','S')
        AND has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
    ) OR EXISTS (SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%'
       AND n.nspname <> 'information_schema' AND has_schema_privilege(current_user,n.oid,'CREATE'))
      OR EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
       WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND c.relkind='S'
       AND has_sequence_privilege(current_user,c.oid,'USAGE,UPDATE'))
      OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND (p.prosecdef OR p.provolatile='v')
       AND has_function_privilege(current_user,p.oid,'EXECUTE'))
      OR EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
       WHERE n.nspname='pg_catalog' AND p.proname IN ('pg_notify','lo_create','lo_creat','lo_import','lo_unlink','lo_put')
       AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS unsafe""").fetchone()
    if unsafe['unsafe']:
        raise RuntimeError('recovery_role_has_write_capabilities')


@app.middleware('http')
async def recovery_read_only_http(request: Request, call_next):
    if RECOVERY_READ_ONLY and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return JSONResponse({'detail': 'read_only_recovery_mode'}, status_code=405)
    return await call_next(request)


@app.on_event("shutdown")
def shutdown_event() -> None:
    close_pool()


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(BASE_DIR / "web" / "static" / "img" / "favicon.ico")


@app.get("/sw.js", include_in_schema=False)
def service_worker() -> FileResponse:
    return FileResponse(
        BASE_DIR / "web" / "static" / "js" / "sw.js",
        media_type="application/javascript",
    )


@app.get("/{filename}.txt", response_class=PlainTextResponse, include_in_schema=False)
def indexnow_key_file(filename: str) -> PlainTextResponse:
    key = os.getenv("INDEXNOW_KEY", "").strip()
    if not key or filename != key:
        raise HTTPException(status_code=404, detail="Not Found")

    return PlainTextResponse(key, media_type="text/plain; charset=utf-8")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.add_middleware(ApiRateLimitMiddleware)


SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'; "
        "frame-src 'none'; "
        "worker-src 'self'; "
        "manifest-src 'self'"
    ),
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": (
        "accelerometer=(), "
        "ambient-light-sensor=(), "
        "autoplay=(), "
        "battery=(), "
        "camera=(), "
        "clipboard-read=(), "
        "clipboard-write=(), "
        "display-capture=(), "
        "document-domain=(), "
        "encrypted-media=(), "
        "fullscreen=(self), "
        "geolocation=(self), "
        "gyroscope=(), "
        "magnetometer=(), "
        "microphone=(), "
        "midi=(), "
        "payment=(), "
        "picture-in-picture=(), "
        "publickey-credentials-get=(), "
        "screen-wake-lock=(), "
        "sync-xhr=(), "
        "usb=(), "
        "web-share=(self), "
        "xr-spatial-tracking=(), "
        "browsing-topics=()"
    ),
    "X-Content-Type-Options": "nosniff",
}


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    for header_name, header_value in SECURITY_HEADERS.items():
        response.headers.setdefault(header_name, header_value)
    return response


@app.middleware("http")
async def localize_html_response(request: Request, call_next):
    response = await call_next(request)
    lang = get_language_from_path(request.url.path)
    content_type = response.headers.get("content-type", "")
    if lang != "va" or "text/html" not in content_type.lower():
        if "text/html" in content_type.lower():
            response.headers.setdefault("Content-Language", "es")
        return response

    body = b""
    async for chunk in response.body_iterator:
        body += chunk

    charset = "utf-8"
    for part in content_type.split(";"):
        part = part.strip().lower()
        if part.startswith("charset="):
            charset = part.split("=", 1)[1]
            break

    html = body.decode(charset, errors="replace")
    localized = translate_html(html, lang).encode(charset)
    headers = dict(response.headers)
    headers.pop("content-length", None)
    headers["Content-Language"] = "ca-ES-valencia"
    return Response(
        content=localized,
        status_code=response.status_code,
        headers=headers,
        media_type=response.media_type,
        background=response.background,
    )


@app.exception_handler(StarletteHTTPException)
async def custom_http_exception_handler(request: Request, exc: StarletteHTTPException):
    if exc.status_code != 404:
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    path = request.url.path
    accept = request.headers.get("accept", "")
    wants_html = "text/html" in accept.lower()

    if path.startswith("/api/") or path.startswith("/static/") or not wants_html:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})

    from app.web.routes import seo_context

    return TEMPLATES.TemplateResponse(
        request=request,
        name="404.html",
        context=seo_context(
            request,
            active_page="not-found",
            page_title="funkcionario.com | Funk not found",
            page_description="Página no encontrada en Funkcionario.com.",
            path="/404",
            robots_meta="noindex,nofollow",
        ),
        status_code=404,
    )


class PgCompatRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PgCompatCursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgCompatRow(row)

    def fetchall(self):
        return [PgCompatRow(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class PgCompatConnection:
    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params: Sequence[Any] | None = None) -> PgCompatCursor:
        # Compatibilidad práctica para reutilizar el SQL actual.
        sql_pg = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql_pg, params or [])
        return PgCompatCursor(cur)

    def close(self) -> None:
        # El cierre real lo controla el pool / context manager.
        return None

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


@contextmanager
def get_connection() -> Iterable[PgCompatConnection]:
    with get_pool().connection() as conn:
        yield PgCompatConnection(conn)


def _register_normalize_function(conn) -> None:
    # Ya no se registra nada en runtime.
    # PostgreSQL lo resuelve con la función normalize_text() definida en schema.sql
    return None


class PushSubscriptionKeysPayload(BaseModel):
    p256dh: str
    auth: str


class PushSubscriptionPayload(BaseModel):
    endpoint: str
    keys: PushSubscriptionKeysPayload


class PushUnsubscribePayload(BaseModel):
    endpoint: str


# ---------- helpers ----------

def rows_to_dicts(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
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

ALLOWED_CENTER_ORDER_FIELDS = {
    "center_code": "c.center_code",
    "denomination": "c.denomination",
    "locality": "c.locality",
    "province": "c.province",
    "regime": "c.regime",
}


def build_order_by(field: str, direction: str, allowed: dict[str, str], fallback: str) -> str:
    sql_field = allowed.get(field, fallback)
    sql_dir = "DESC" if direction.lower() == "desc" else "ASC"
    return f" ORDER BY {sql_field} {sql_dir} "


def build_distance_order_by(direction: str, secondary_sql: str) -> str:
    """
    Ordena por distancia real Haversine en PostgreSQL cuando el cliente envía
    origin_lat/origin_lon. Mantiene los centros sin coordenadas al final.
    """
    sql_dir = "DESC" if direction.lower() == "desc" else "ASC"

    return f"""
        ORDER BY
            CASE WHEN c.latitude IS NULL OR c.longitude IS NULL THEN 1 ELSE 0 END ASC,
            (
                2 * 6371.0088 * ASIN(
                    LEAST(
                        1.0::double precision,
                        SQRT(
                            POWER(SIN(RADIANS((CAST(c.latitude AS double precision) - ?) / 2.0)), 2)
                            + COS(RADIANS(?)) * COS(RADIANS(CAST(c.latitude AS double precision)))
                            * POWER(SIN(RADIANS((CAST(c.longitude AS double precision) - ?) / 2.0)), 2)
                        )
                    )
                )
            ) {sql_dir},
            {secondary_sql}
    """


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

def enrich_center_geo_fields(
    item: dict[str, Any],
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> dict[str, Any]:
    lat = item.get("center_latitude")
    lon = item.get("center_longitude")

    item["center_maps_url"] = None
    item["center_directions_url"] = None
    item["distance_km"] = None

    if lat is None or lon is None:
        return item

    lat_f = float(lat)
    lon_f = float(lon)

    item["center_maps_url"] = build_google_maps_search_url(lat_f, lon_f)
    item["center_directions_url"] = build_google_maps_directions_url(
        lat_f,
        lon_f,
        origin_lat,
        origin_lon,
    )

    if origin_lat is not None and origin_lon is not None:
        item["distance_km"] = round(
            haversine_km(float(origin_lat), float(origin_lon), lat_f, lon_f),
            2,
        )

    return item


def center_row_to_payload(
    row: dict[str, Any],
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> dict[str, Any]:
    item = dict(row)
    if item.get('data_source') == 'offered_positions':
        item['validation_status'] = 'needs_validation'

    lat = item.get("latitude")
    lon = item.get("longitude")

    item["maps_url"] = None
    item["directions_url"] = None
    item["distance_km"] = None

    if lat is not None and lon is not None:
        lat_f = float(lat)
        lon_f = float(lon)

        item["maps_url"] = build_google_maps_search_url(lat_f, lon_f)
        item["directions_url"] = build_google_maps_directions_url(
            lat_f,
            lon_f,
            origin_lat,
            origin_lon,
        )

        if origin_lat is not None and origin_lon is not None:
            item["distance_km"] = round(
                haversine_km(float(origin_lat), float(origin_lon), lat_f, lon_f),
                2,
            )

    return item


def build_user_view(
    person: dict[str, Any],
    awards: list[dict[str, Any]],
    difficult_coverage: list[dict[str, Any]],
    bag_memberships: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    bag_memberships = bag_memberships or []

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
        "assigned_center_code": None,
        "assigned_center_address": None,
        "assigned_center_phone": None,
        "assigned_center_latitude": None,
        "assigned_center_longitude": None,
        "assigned_center_maps_url": None,
        "assigned_center_directions_url": None,
        "assigned_distance_km": None,
        "bag_position": None,
        "participant_order": None,
        "participant_collective": None,
        "bag_course_year": None,
        "bag_list_stage": None,
        "bag_service_status": None,
        "bag_collective": None,
        "bag_habilitations": None,
        "bag_position_scope": None,
    }

    # A summary describes the latest dated publication, never an administrative
    # status inferred from an arbitrary historical award. Unknown dates and ties
    # across documents/scopes require the user to consult the history.
    records = (
        [("award", row) for row in awards]
        + [("difficult", row) for row in difficult_coverage]
        + [("bag", row) for row in bag_memberships]
    )

    if any(row.get('publication_date_quality') != 'official' for _, row in records):
        return {**base, 'current_result_label': 'Cronología no acreditada',
                'current_result_message': 'La fecha de publicación no está acreditada. Consulta cada documento; no se puede determinar la situación actual.'}

    def date_key(row: dict[str, Any]) -> str | None:
        try:
            return date.fromisoformat(str(row.get("document_date_iso") or "")[:10]).isoformat()
        except ValueError:
            return None

    def course(row: dict[str, Any]) -> str | None:
        value = row.get("course_year")
        if value:
            return str(value)
        filename = str(row.get("original_filename") or "")
        match = re.match(r"ini_(20\d{2})_", filename)
        if match:
            year = int(match.group(1))
            return f"{year}/{year + 1}"
        match = re.match(r"\d{6}_lis_(?:mae|sec)", filename)
        day = date_key(row)
        if match and day:
            parsed = date.fromisoformat(day)
            year = parsed.year if parsed.month >= 9 else parsed.year - 1
            return f"{year}/{year + 1}"
        return None

    if not records or any(date_key(row) is None for _, row in records):
        return {**base, "current_result_message": "Faltan fechas fiables para resumir las publicaciones. Consulta el historial; no se puede deducir la situación administrativa actual."}
    latest_date = max(date_key(row) for _, row in records)
    latest = [(kind, row) for kind, row in records if date_key(row) == latest_date]
    known_courses = [course(row) for _, row in records if course(row)]
    if known_courses and any(course(row) and course(row) < max(known_courses) for _, row in latest):
        return {**base, "latest_date": latest_date, "current_result_message": "La publicación con fecha posterior pertenece a un curso anterior. Consulta cada curso y procedimiento en el historial."}
    if len(latest) != 1:
        return {**base, "current_result": "ambiguous", "latest_date": latest_date,
                "current_result_label": "Varios resultados en la misma fecha",
                "current_result_message": "Hay varios resultados, ámbitos o procedimientos en la fecha más reciente. Consulta cada publicación; no se deduce una situación administrativa única."}
    kind, latest_row = latest[0]
    latest_award = latest_row if kind == "award" else None
    latest_bag = latest_row if kind == "bag" else None
    latest_awarded = (
        latest_award
        if latest_award
        and latest_award.get("status") == "Adjudicat"
        and latest_award.get("assignments_count", len(latest_award.get("assignments", []))) > 0
        else None
    )
    selected_difficult = latest_row if kind == "difficult" and latest_row.get("is_selected") is True else None
    base["result_context"] = {"course_year": course(latest_row), "procedure": latest_row.get("procedure") or latest_row.get("staff_kind"),
                              "list_scope": latest_row.get("list_scope"), "specialty_code": latest_row.get("specialty_code")}

    if latest_awarded:
        assignments = latest_awarded.get("assignments") or []
        first_assignment = assignments[0] if assignments else None
        return {
            **base,
            "current_result": "awarded",
            "current_result_label": "Adjudicado",
            "current_result_message": "En la publicación más reciente consultada consta una adjudicación en su curso y procedimiento. No acredita la situación administrativa actual.",
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
            "assigned_center_code": first_assignment.get("center_code") if first_assignment else None,
            "assigned_center_address": first_assignment.get("center_full_address") if first_assignment else None,
            "assigned_center_phone": first_assignment.get("center_phone") if first_assignment else None,
            "assigned_center_latitude": first_assignment.get("center_latitude") if first_assignment else None,
            "assigned_center_longitude": first_assignment.get("center_longitude") if first_assignment else None,
            "assigned_center_maps_url": first_assignment.get("center_maps_url") if first_assignment else None,
            "assigned_center_directions_url": first_assignment.get("center_directions_url") if first_assignment else None,
            "assigned_distance_km": first_assignment.get("distance_km") if first_assignment else None,
        }

    if latest_bag:
        staff_kind = latest_bag.get("staff_kind")
        if staff_kind != "interino":
            career = staff_kind == "career_practice"
            return {
                **base,
                "current_result": "career_practice_participant" if career else "participant_type_unknown",
                "current_result_label": "Participante de carrera/prácticas" if career else "Procedimiento sin identificar",
                "current_result_message": "Figura en un listado de participantes de carrera/prácticas." if career else "Figura en un listado cuyo tipo de procedimiento no está acreditado.",
                "latest_scope_label": "Carrera/prácticas" if career else "Procedimiento sin identificar",
                "latest_specialty_label": make_label(latest_bag.get("specialty_code"), latest_bag.get("specialty_name")),
                "latest_date": latest_bag.get("document_date_iso"),
                "participant_order": latest_bag.get("order_number"),
                "participant_collective": latest_bag.get("collective"),
                "bag_course_year": latest_bag.get("course_year"),
                "bag_list_stage": latest_bag.get("list_stage"),
            }
        position_scope = latest_bag.get("position_scope")
        scope_label = "Bolsa general" if position_scope == "general" else "Bolsa por especialidad"
        service_or_collective = latest_bag.get("service_status") or latest_bag.get("collective")
        return {
            **base,
            "current_result": "docent_bag_member",
            "current_result_label": "Consta en bolsa docente",
            "current_result_message": "Figura en un listado de bolsa o participantes de inicio de curso cargado en la web.",
            "latest_scope_label": scope_label,
            "latest_specialty_label": make_label(
                latest_bag.get("specialty_code"),
                latest_bag.get("specialty_name"),
            ) or map_list_scope_label(latest_bag.get("list_scope")),
            "latest_date": latest_bag.get("document_date_iso"),
            "recommended_action": "Es un listado provisional si así lo indica la publicación oficial. Revisa futuras publicaciones definitivas o adjudicaciones.",
            "bag_position": latest_bag.get("order_number"),
            "bag_course_year": latest_bag.get("course_year"),
            "bag_list_stage": latest_bag.get("list_stage"),
            "bag_service_status": service_or_collective,
            "bag_collective": latest_bag.get("collective"),
            "bag_habilitations": latest_bag.get("habilitations"),
            "bag_position_scope": position_scope,
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
                "current_result_message": "La publicación más reciente consultada indica que no hubo adjudicación en ese procedimiento; no describe otros ámbitos ni la situación administrativa actual.",
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
                "current_result_message": "La publicación más reciente consultada indica desactivación en ese procedimiento; no determina la situación en otros cursos o ámbitos.",
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
            "current_result_message": "En la publicación más reciente consultada consta selección en difícil cobertura; verifica su fecha y ámbito en la fuente oficial.",
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
            "assigned_center_code": selected_difficult.get("center_code"),
            "assigned_center_address": selected_difficult.get("center_full_address"),
            "assigned_center_phone": selected_difficult.get("center_phone"),
            "assigned_center_latitude": selected_difficult.get("center_latitude"),
            "assigned_center_longitude": selected_difficult.get("center_longitude"),
            "assigned_center_maps_url": selected_difficult.get("center_maps_url"),
            "assigned_center_directions_url": selected_difficult.get("center_directions_url"),
            "assigned_distance_km": selected_difficult.get("distance_km"),
        }

    if kind == "difficult":
        latest_difficult = latest_row
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



# ---------- docent summary ----------

@app.get("/api/docent/summary")
def get_docent_summary() -> dict[str, Any]:
    tracked_doc_families = (
        "offered_positions",
        "final_award_listing",
        "difficult_coverage_provisional",
        "docent_bag_participants",
        "docent_career_practice_participants",
        "docent_bag_resolution",
        "docent_career_practice_resolution",
    )
    placeholders = ",".join("%s" for _ in tracked_doc_families)

    with get_connection() as conn:
        row = conn.execute(
            f"""
            SELECT
                MAX(d.document_date_iso) AS latest_date,
                COUNT(*) AS documents_count
            FROM documents d
            WHERE d.doc_family IN ({placeholders})
              AND d.document_date_iso IS NOT NULL
            """,
            tracked_doc_families,
        ).fetchone()

        latest_by_family = rows_to_dicts(
            conn.execute(
                f"""
                SELECT
                    d.doc_family,
                    MAX(d.document_date_iso) AS latest_date,
                    COUNT(*) AS documents_count
                FROM documents d
                WHERE d.doc_family IN ({placeholders})
                GROUP BY d.doc_family
                ORDER BY MAX(d.document_date_iso) DESC NULLS LAST, d.doc_family ASC
                """,
                tracked_doc_families,
            ).fetchall()
        )

    return {
        "latest_date": row["latest_date"] if row else None,
        "documents_count": int(row["documents_count"] or 0) if row else 0,
        "latest_by_family": latest_by_family,
    }


# ---------- public alerts ----------

@app.get("/api/public-alerts")
def api_public_alerts(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    with get_connection() as conn:
        items = list_public_alerts(conn, limit=limit, offset=offset)
        total = count_public_alerts(conn)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------- push ----------

@app.get("/api/push/public-key")
def get_push_public_key() -> dict[str, Any]:
    configured = is_push_configured()
    return {
        "configured": configured,
        "public_key": get_vapid_public_key() if configured else None,
    }


@app.post("/api/push/subscribe")
def subscribe_push(payload: PushSubscriptionPayload) -> dict[str, Any]:
    if not is_push_configured():
        raise HTTPException(
            status_code=503,
            detail="Push notifications are not configured",
        )

    with get_connection() as conn:
        try:
            upsert_push_subscription(
                conn,
                endpoint=payload.endpoint,
                p256dh_key=payload.keys.p256dh,
                auth_key=payload.keys.auth,
            )
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"ok": True, "endpoint": payload.endpoint}


@app.post("/api/push/unsubscribe")
def unsubscribe_push(payload: PushUnsubscribePayload) -> dict[str, Any]:
    with get_connection() as conn:
        try:
            deactivate_push_subscription(conn, payload.endpoint)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {"ok": True, "endpoint": payload.endpoint}



# ---------- health ----------

@app.get("/health")
def health() -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        if RECOVERY_READ_ONLY:
            try:
                normalized = conn.execute("SELECT public.normalize_text(?)", ("GARCÍA",)).fetchone()[0]
            except Exception as exc:
                raise HTTPException(status_code=503, detail="recovery_search_unavailable") from exc
            if normalized != "garcia":
                raise HTTPException(status_code=503, detail="recovery_search_unavailable")
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
            "non_docent_publications": conn.execute(
                "SELECT COUNT(*) FROM non_docent_publications"
            ).fetchone()[0],
            "non_docent_offered_positions": conn.execute(
                "SELECT COUNT(*) FROM non_docent_offered_positions"
            ).fetchone()[0],
            "non_docent_awards": conn.execute(
                "SELECT COUNT(*) FROM non_docent_awards"
            ).fetchone()[0],
            "non_docent_bag_members": conn.execute(
                "SELECT COUNT(*) FROM non_docent_bag_members"
            ).fetchone()[0],
            "docent_bag_members": conn.execute(
                "SELECT COUNT(*) FROM docent_bag_members"
            ).fetchone()[0],
            "centers": conn.execute("SELECT COUNT(*) FROM centers").fetchone()[0],
            "push_subscriptions": conn.execute(
                "SELECT COUNT(*) FROM push_subscriptions WHERE is_active = TRUE"
            ).fetchone()[0],
            "public_alerts": conn.execute("SELECT COUNT(*) FROM public_alerts").fetchone()[0],
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
        "db_engine": DB_PATH,
        "push_configured": is_push_configured(),
        "counts": counts,
        "latest_documents": latest_docs,
    }


# ---------- center search ----------

@app.get("/api/centers/options")
def get_center_filter_options() -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        provinces = [
            row[0]
            for row in conn.execute(
                _CENTER_SEARCH_SOURCE + " SELECT DISTINCT province FROM center_source WHERE province IS NOT NULL AND province <> '' ORDER BY province ASC"
            ).fetchall()
        ]
        localities = [
            row[0]
            for row in conn.execute(
                _CENTER_SEARCH_SOURCE + " SELECT DISTINCT locality FROM center_source WHERE locality IS NOT NULL AND locality <> '' ORDER BY locality ASC"
            ).fetchall()
        ]
        regimes = [
            row[0]
            for row in conn.execute(
                _CENTER_SEARCH_SOURCE + " SELECT DISTINCT regime FROM center_source WHERE regime IS NOT NULL AND regime <> '' ORDER BY regime ASC"
            ).fetchall()
        ]

    return {"provinces": provinces, "localities": localities, "regimes": regimes}


_CENTER_SEARCH_SOURCE = """
WITH center_source AS (
    SELECT center_code, denomination, generic_name_es, generic_name_val,
           specific_name, regime, postal_code, locality, province, comarca,
           phone, fax, latitude, longitude, full_address, 'catalog'::text AS data_source
    FROM centers
    UNION ALL
    SELECT op.center_code,
           CASE WHEN count(DISTINCT NULLIF(trim(op.center_name), '')) = 1
                THEN min(NULLIF(trim(op.center_name), '')) END,
           NULL::text, NULL::text, NULL::text, NULL::text, NULL::text,
           CASE WHEN count(DISTINCT NULLIF(trim(op.locality), '')) = 1
                THEN min(NULLIF(trim(op.locality), '')) END,
           CASE WHEN count(DISTINCT NULLIF(trim(op.province), '')) = 1
                THEN min(NULLIF(trim(op.province), '')) END,
           NULL::text, NULL::text, NULL::text, NULL::double precision,
           NULL::double precision, NULL::text, 'offered_positions'::text
    FROM offered_positions op
    WHERE op.center_code IS NOT NULL AND trim(op.center_code) <> ''
      AND NOT EXISTS (SELECT 1 FROM centers known WHERE known.center_code = op.center_code)
    GROUP BY op.center_code
    HAVING count(DISTINCT NULLIF(trim(op.center_name), '')) = 1
)
"""


@app.get("/api/centers")
def search_centers(
    q: str | None = Query(None, description="Texto libre sobre código, centro, dirección o localidad"),
    province: str | None = None,
    locality: str | None = None,
    regime: str | None = None,
    origin_lat: float | None = Query(None, description="Latitud opcional del origen para calcular distancia"),
    origin_lon: float | None = Query(None, description="Longitud opcional del origen para calcular distancia"),
    order_by: str = Query("denomination"),
    order_dir: str = Query("asc", pattern="^(asc|desc)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    if q:
        pattern = f"%{q.strip()}%"
        where.append(
            "("
            "normalize_text(c.center_code) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.denomination, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.generic_name_es, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.generic_name_val, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.specific_name, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.full_address, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.locality, '')) LIKE normalize_text(?) OR "
            "normalize_text(COALESCE(c.province, '')) LIKE normalize_text(?)"
            ")"
        )
        params.extend([pattern] * 8)
    if province:
        where.append("normalize_text(c.province) = normalize_text(?)")
        params.append(province)
    if locality:
        where.append("normalize_text(c.locality) = normalize_text(?)")
        params.append(locality)
    if regime:
        where.append("normalize_text(c.regime) = normalize_text(?)")
        params.append(regime)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    base_sql = f"FROM center_source c {where_sql}"
    count_sql = _CENTER_SEARCH_SOURCE + " SELECT COUNT(*) " + base_sql

    distance_sort = order_by == "distance" and origin_lat is not None and origin_lon is not None
    if distance_sort:
        items_sql = (
            """
            SELECT
                c.center_code,
                c.denomination,
                c.generic_name_es,
                c.generic_name_val,
                c.specific_name,
                c.regime,
                c.postal_code,
                c.locality,
                c.province,
                c.comarca,
                c.phone,
                c.fax,
                c.latitude,
                c.longitude,
                c.full_address,
                c.data_source
            """
            + base_sql
        )
    else:
        order_sql = build_order_by(order_by, order_dir, ALLOWED_CENTER_ORDER_FIELDS, "c.denomination")
        items_sql = (
            """
            SELECT
                c.center_code,
                c.denomination,
                c.generic_name_es,
                c.generic_name_val,
                c.specific_name,
                c.regime,
                c.postal_code,
                c.locality,
                c.province,
                c.comarca,
                c.phone,
                c.fax,
                c.latitude,
                c.longitude,
                c.full_address,
                c.data_source
            """
            + base_sql
            + order_sql
            + " LIMIT ? OFFSET ? "
        )

    items_sql = _CENTER_SEARCH_SOURCE + items_sql
    with get_connection() as conn:
        _register_normalize_function(conn)
        total = conn.execute(count_sql, params).fetchone()[0]
        if distance_sort:
            raw_items = rows_to_dicts(conn.execute(items_sql, params).fetchall())
        else:
            raw_items = rows_to_dicts(conn.execute(items_sql, [*params, limit, offset]).fetchall())

    items = [center_row_to_payload(item, origin_lat, origin_lon) for item in raw_items]
    if distance_sort:
        reverse = order_dir.lower() == "desc"
        items.sort(
            key=lambda item: (
                item.get("distance_km") is None,
                item.get("distance_km") if item.get("distance_km") is not None else 0,
                item.get("denomination") or "",
            ),
            reverse=reverse,
        )
        items = items[offset : offset + limit]

    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/centers/{center_code}")
def get_center_detail(
    center_code: str,
    origin_lat: float | None = Query(None),
    origin_lon: float | None = Query(None),
) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        center = conn.execute(_CENTER_SEARCH_SOURCE + " SELECT * FROM center_source WHERE center_code = ?", [center_code]).fetchone()
        if center is None:
            raise HTTPException(status_code=404, detail="center_code no encontrado")

    return {
        "center": center_row_to_payload(center, origin_lat, origin_lon),
    }


def enrich_document_provenance(conn, items: list[dict[str, Any]]) -> None:
    """Attach only attestations tied to the current successful parse run.

    JSON access keeps older schemas readable until an explicit migration adds the
    optional attestation fields. Missing or stale attestations never imply validity.
    """
    ids = sorted({x['document_id'] for x in items if x.get('document_id') is not None})
    if not ids:
        return
    rows = conn.execute("""
        SELECT d.id, v.sha256, to_jsonb(d)->>'validation_status' AS validation_status,
               to_jsonb(d)->>'validation_parse_run_id' AS validation_run,
               to_jsonb(d)->>'historical_status' AS historical_status,
               to_jsonb(d)->>'publication_date_quality' AS publication_date_quality,
               (SELECT r.id::text FROM document_parse_runs r
                WHERE r.document_version_id=d.document_version_id AND r.status='success'
                ORDER BY r.id DESC LIMIT 1) AS latest_success
        FROM documents d JOIN document_versions v ON v.id=d.document_version_id
        WHERE d.id = ANY(?)
    """, [ids]).fetchall()
    evidence = {row['id']: row for row in rows}
    relationships: dict[int, list[dict[str, Any]]] = {}
    batch_evidence: dict[int, dict[str, Any]] = {}
    if conn.execute("SELECT to_regclass('recovery_batch.documents')").fetchone()[0] is not None:
        batch_rows=conn.execute('''SELECT target_document_id,source_rows,source_total,quality FROM recovery_batch.documents
            WHERE target_document_id=ANY(?)''',[ids]).fetchall()
        batch_evidence={r['target_document_id']:r for r in batch_rows}
    if conn.execute("SELECT to_regclass('public.historical_procedure_documents')").fetchone()[0] is not None:
        relations = conn.execute('''SELECT pd.document_id, p.procedure_key, p.procedure_kind,
                    p.procedure_date::text AS procedure_date, p.date_evidence,
                    p.publication_date::text AS publication_date, p.publication_date_quality,
                    pd.document_role, pd.relationship_evidence
                FROM historical_procedure_documents pd
                JOIN historical_procedures p ON p.procedure_key=pd.procedure_key
                WHERE pd.document_id=ANY(?) ORDER BY p.procedure_date,p.procedure_key''', [ids]).fetchall()
        for relation in relations:
            item = dict(relation)
            relationships.setdefault(item.pop('document_id'), []).append(item)
    for item in items:
        row = evidence.get(item.get('document_id'))
        if row is None:
            continue
        valid = row['validation_run'] is not None and row['validation_run'] == row['latest_success']
        item['validation_status'] = row['validation_status'] if valid and row['validation_status'] in ('validated', 'validated_with_limitations', 'needs_validation') else 'needs_validation'
        item['historical_status'] = row['historical_status'] if row['historical_status'] in ('accredited', 'quarantine', 'unknown') else 'unknown'
        item['publication_date_quality'] = row['publication_date_quality'] if row['publication_date_quality'] in ('official', 'inferred', 'unknown') else 'unknown'
        item['source_document_sha256'] = row['sha256']
        item['procedures'] = relationships.get(item['document_id'], [])
        batch=batch_evidence.get(item['document_id'])
        if batch and batch['source_total'] is not None:
            item['extraction_completeness']='partial' if batch['source_rows']<batch['source_total'] else 'complete_with_limitations'
            item['pending_source_rows']=batch['source_total']-batch['source_rows']
            item['reconciliation_quality']=batch['quality']


def mask_unaccredited_availability(items: list[dict[str, Any]]) -> None:
    """A historical extraction is not evidence of present availability."""
    for item in items:
        if (RECOVERY_READ_ONLY or item.get('historical_status') != 'accredited') and item.get('availability_status') == 'available':
            item['availability_status'] = 'unknown'
            item['availability_reason'] = 'vigencia_no_acreditada'


@app.get('/api/historical-procedures')
def list_historical_procedures() -> dict[str, Any]:
    with get_connection() as conn:
        if conn.execute("SELECT to_regclass('public.historical_procedures')").fetchone()[0] is None:
            return {'items': [], 'status': 'not_configured'}
        items = rows_to_dicts(conn.execute('''SELECT procedure_key, procedure_kind,
            procedure_date::text AS procedure_date, date_evidence,
            publication_date::text AS publication_date, publication_date_quality
            FROM historical_procedures ORDER BY procedure_date,procedure_key''').fetchall())
    return {'items':items, 'status':'available'}


@app.get('/api/historical-procedures/{procedure_key}')
def get_historical_procedure(procedure_key: str) -> dict[str, Any]:
    with get_connection() as conn:
        if conn.execute("SELECT to_regclass('public.historical_procedures')").fetchone()[0] is None:
            raise HTTPException(status_code=404, detail='procedimientos históricos no configurados')
        procedure=conn.execute('''SELECT procedure_key,procedure_kind,procedure_date::text AS procedure_date,
            date_evidence,publication_date::text AS publication_date,publication_date_quality
            FROM historical_procedures WHERE procedure_key=?''',[procedure_key]).fetchone()
        if procedure is None:
            raise HTTPException(status_code=404,detail='procedimiento no encontrado')
        docs=rows_to_dicts(conn.execute('''SELECT d.id AS document_id,pd.document_role,pd.relationship_evidence,
            d.doc_family,d.title, CASE
              WHEN d.doc_family IN ('final_award_listing','displaced_award_listing','religion_award_listing')
                THEN (SELECT count(*) FROM award_results r WHERE r.document_id=d.id)
              WHEN d.doc_family='offered_positions'
                THEN (SELECT count(*) FROM offered_positions op WHERE op.document_id=d.id)
              WHEN d.doc_family='unawarded_expert_vacancies'
                THEN (SELECT count(*) FROM historical_unawarded_expert_vacancies u WHERE u.document_id=d.id)
              WHEN EXISTS (SELECT 1 FROM historical_unawarded_expert_vacancies u WHERE u.document_id=d.id)
                THEN (SELECT count(*) FROM historical_unawarded_expert_vacancies u WHERE u.document_id=d.id)
              ELSE NULL END AS extracted_rows
            FROM historical_procedure_documents pd JOIN documents d ON d.id=pd.document_id
            WHERE pd.procedure_key=? ORDER BY d.id''',[procedure_key]).fetchall())
        enrich_document_provenance(conn,docs)
        source_links={}
        if conn.execute("SELECT to_regclass('recovery_03nov.document_evidence')").fetchone()[0] is not None:
            links=conn.execute('''SELECT m.target_document_id,e.canonical_url FROM recovery_03nov.document_evidence e
                JOIN recovery_03nov.document_map m ON m.source_sha=e.source_sha''').fetchall()
            source_links.update({r['target_document_id']:(r['canonical_url'],'verified_inventory_reference') for r in links})
        if conn.execute("SELECT to_regclass('recovery_0519.document_map')").fetchone()[0] is not None:
            links=conn.execute('SELECT target_document_id,canonical_url,page_url,html_sha,evidence_quality FROM recovery_0519.document_map').fetchall()
            source_links.update({r['target_document_id']:(r['canonical_url'],r['evidence_quality']) for r in links})
        for item in docs:
            if item['document_id'] in source_links:
                item['official_document_url'],item['document_url_evidence']=source_links[item['document_id']]
        # Historical alternate offer lists can be retained without presenting
        # either version as a current offer. The table is optional on older DBs.
        if conn.execute("SELECT to_regclass('recovery_quarantine.positions_19nov')").fetchone()[0] is not None:
            pending=conn.execute('''SELECT document_id,count(*) AS n,min(stage_label) AS stage
                FROM recovery_quarantine.positions_19nov GROUP BY document_id''').fetchall()
            pending_by_document={row['document_id']:row for row in pending}
            for item in docs:
                row=pending_by_document.get(item['document_id'])
                if row:
                    item['quarantined_rows']=row['n']
                    item['extracted_rows']=row['n']
                    item['extraction_storage']='quarantined'
                    item['document_stage_label']=row['stage']
                    item['stage_evidence']='url_label_observed_bytes_at_capture_unverified'
        unawarded=rows_to_dicts(conn.execute('''SELECT u.document_id,u.center_code,u.source_position_id,
            u.province,u.locality,u.center_name,u.cycle,u.modules,u.teaching_hours,u.total_hours,u.result_status
            FROM historical_unawarded_expert_vacancies u JOIN historical_procedure_documents pd ON pd.document_id=u.document_id
            WHERE pd.procedure_key=? ORDER BY u.center_code,u.source_position_id''',[procedure_key]).fetchall())
        enrich_document_provenance(conn,unawarded)
    return {'procedure':dict(procedure),'documents':docs,'unawarded_vacancies':unawarded}


# ---------- persons search ----------

@app.get("/api/search/persons")
def search_persons(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    pattern = f"%{q}%"
    with get_connection() as conn:
        sql = """
        WITH award_people AS (
            SELECT
                normalize_text(ar.person_display_name) AS normalized_name,
                MIN(ar.person_display_name) AS display_name,
                'award_results' AS source_kind,
                COUNT(*) AS total_records,
                SUM(CASE WHEN ar.status = 'Adjudicat' THEN 1 ELSE 0 END) AS total_awarded,
                0::bigint AS total_difficult_positions,
                0::bigint AS total_bag_records,
                0::bigint AS total_career_practice_records,
                0::bigint AS total_unidentified_procedure_records
            FROM award_results ar
            WHERE normalize_text(ar.person_display_name) LIKE normalize_text(?)
               OR normalize_text(ar.person_name_normalized) LIKE normalize_text(?)
            GROUP BY normalize_text(ar.person_display_name)
        ),
        difficult_people AS (
            SELECT
                normalize_text(dc.full_name) AS normalized_name,
                MIN(dc.full_name) AS display_name,
                'difficult_coverage_candidates' AS source_kind,
                COUNT(*) AS total_records,
                SUM(CASE WHEN dc.is_selected IS TRUE THEN 1 ELSE 0 END) AS total_awarded,
                COUNT(DISTINCT dc.position_id) AS total_difficult_positions,
                0::bigint AS total_bag_records,
                0::bigint AS total_career_practice_records,
                0::bigint AS total_unidentified_procedure_records
            FROM difficult_coverage_candidates dc
            WHERE normalize_text(dc.full_name) LIKE normalize_text(?)
               OR normalize_text(dc.full_name_normalized) LIKE normalize_text(?)
            GROUP BY normalize_text(dc.full_name)
        ),
        bag_people AS (
            SELECT
                normalize_text(dbm.person_display_name) AS normalized_name,
                MIN(dbm.person_display_name) AS display_name,
                'docent_bag_members' AS source_kind,
                COUNT(*) AS total_records,
                0::bigint AS total_awarded,
                0::bigint AS total_difficult_positions,
                COUNT(*) FILTER (WHERE dbs.staff_kind = 'interino') AS total_bag_records,
                COUNT(*) FILTER (WHERE dbs.staff_kind = 'career_practice') AS total_career_practice_records,
                COUNT(*) FILTER (WHERE dbs.staff_kind IS NULL OR dbs.staff_kind NOT IN ('interino','career_practice')) AS total_unidentified_procedure_records
            FROM docent_bag_members dbm
            JOIN docent_bag_snapshots dbs ON dbs.id = dbm.snapshot_id
            WHERE normalize_text(dbm.person_display_name) LIKE normalize_text(?)
               OR normalize_text(dbm.person_name_normalized) LIKE normalize_text(?)
            GROUP BY normalize_text(dbm.person_display_name)
        ),
        unioned AS (
            SELECT * FROM award_people
            UNION ALL
            SELECT * FROM difficult_people
            UNION ALL
            SELECT * FROM bag_people
        )
        SELECT
            normalized_name,
            MIN(display_name) AS display_name,
            SUM(total_records) AS total_records,
            SUM(total_awarded) AS total_awarded,
            SUM(total_difficult_positions) AS total_difficult_positions,
            SUM(total_bag_records) AS total_bag_records,
            SUM(total_career_practice_records) AS total_career_practice_records,
            SUM(total_unidentified_procedure_records) AS total_unidentified_procedure_records,
            string_agg(source_kind, ',' ORDER BY source_kind) AS source_kinds
        FROM unioned
        GROUP BY normalized_name
        ORDER BY total_records DESC, display_name ASC
        LIMIT ?
        """
        items = rows_to_dicts(
            conn.execute(sql, [pattern, pattern, pattern, pattern, pattern, pattern, limit]).fetchall()
        )
    return {"items": items, "count": len(items), "query": q}

@app.get("/api/persons/profile")
def get_person_profile(
    normalized_name: str = Query(
        ...,
        min_length=2,
        description="Valor exacto devuelto por /api/search/persons -> normalized_name",
    ),
    origin_lat: float | None = Query(None, description="Latitud opcional del origen para calcular distancia"),
    origin_lon: float | None = Query(None, description="Longitud opcional del origen para calcular distancia"),
) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)

        person = conn.execute(
            """
            WITH candidates AS (
                SELECT
                    normalize_text(ar.person_display_name) AS normalized_name,
                    ar.person_display_name AS display_name,
                    COUNT(*) AS total_rows
                FROM award_results ar
                WHERE normalize_text(ar.person_display_name) = normalize_text(?)
                   OR normalize_text(ar.person_name_normalized) = normalize_text(?)
                GROUP BY normalize_text(ar.person_display_name), ar.person_display_name

                UNION ALL

                SELECT
                    normalize_text(dc.full_name) AS normalized_name,
                    dc.full_name AS display_name,
                    COUNT(*) AS total_rows
                FROM difficult_coverage_candidates dc
                WHERE normalize_text(dc.full_name) = normalize_text(?)
                   OR normalize_text(dc.full_name_normalized) = normalize_text(?)
                GROUP BY normalize_text(dc.full_name), dc.full_name

                UNION ALL

                SELECT
                    normalize_text(dbm.person_display_name) AS normalized_name,
                    dbm.person_display_name AS display_name,
                    COUNT(*) AS total_rows
                FROM docent_bag_members dbm
                WHERE normalize_text(dbm.person_display_name) = normalize_text(?)
                   OR normalize_text(dbm.person_name_normalized) = normalize_text(?)
                GROUP BY normalize_text(dbm.person_display_name), dbm.person_display_name
            )
            SELECT
                normalized_name,
                display_name
            FROM candidates
            ORDER BY total_rows DESC, display_name ASC
            LIMIT 1
            """,
            [
                normalized_name, normalized_name,
                normalized_name, normalized_name,
                normalized_name, normalized_name,
            ],
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
                WHERE normalize_text(ar.person_display_name) = normalize_text(?)
                   OR normalize_text(ar.person_name_normalized) = normalize_text(?)
            ),
            assignment_summary AS (
                SELECT
                    COUNT(*) AS total_assignments
                FROM award_assignments aa
                JOIN award_results ar ON ar.id = aa.award_result_id
                WHERE normalize_text(ar.person_display_name) = normalize_text(?)
                   OR normalize_text(ar.person_name_normalized) = normalize_text(?)
            ),
            difficult_summary AS (
                SELECT
                    COUNT(*) AS total_difficult_coverage_candidates,
                    SUM(CASE WHEN dc.is_selected IS TRUE THEN 1 ELSE 0 END) AS total_difficult_selected,
                    COUNT(DISTINCT dc.position_id) AS total_difficult_positions,
                    MAX(d.document_date_iso) AS last_difficult_date
                FROM difficult_coverage_candidates dc
                JOIN difficult_coverage_positions p ON p.id = dc.position_id
                JOIN documents d ON d.id = p.document_id
                WHERE normalize_text(dc.full_name) = normalize_text(?)
                   OR normalize_text(dc.full_name_normalized) = normalize_text(?)
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
            [
                normalized_name, normalized_name,
                normalized_name, normalized_name,
                normalized_name, normalized_name,
            ],
        ).fetchone()

        summary_dict = dict(summary)
        bag_summary = conn.execute(
            """
            SELECT
                COUNT(*) FILTER (WHERE dbs.staff_kind = 'interino') AS total_bag_records,
                COUNT(DISTINCT dbm.snapshot_id) FILTER (WHERE dbs.staff_kind = 'interino') AS total_bag_snapshots,
                MIN(dbm.order_number) FILTER (WHERE dbs.staff_kind = 'interino') AS best_bag_position,
                MAX(d.document_date_iso) FILTER (WHERE dbs.staff_kind = 'interino') AS last_bag_date,
                COUNT(*) FILTER (WHERE dbs.staff_kind = 'career_practice') AS total_career_practice_records,
                MAX(d.document_date_iso) FILTER (WHERE dbs.staff_kind = 'career_practice') AS last_career_practice_date,
                COUNT(*) FILTER (WHERE dbs.staff_kind IS NULL OR dbs.staff_kind NOT IN ('interino','career_practice')) AS total_unidentified_procedure_records,
                MAX(d.document_date_iso) FILTER (WHERE dbs.staff_kind IS NULL OR dbs.staff_kind NOT IN ('interino','career_practice')) AS last_unidentified_procedure_date
            FROM docent_bag_members dbm
            JOIN docent_bag_snapshots dbs ON dbs.id = dbm.snapshot_id
            JOIN documents d ON d.id = dbs.document_id
            WHERE normalize_text(dbm.person_display_name) = normalize_text(?)
               OR normalize_text(dbm.person_name_normalized) = normalize_text(?)
            """,
            [normalized_name, normalized_name],
        ).fetchone()
        summary_dict.update(dict(bag_summary))
        summary_dict["last_seen_date"] = max(
            str(value or "")
            for value in (
                summary_dict.get("last_seen_date"),
                summary_dict.get("last_bag_date"),
                summary_dict.get("last_career_practice_date"),
                summary_dict.get("last_unidentified_procedure_date"),
            )
        ) or None

        awards = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    ar.id,
                    ar.document_id,
                    d.document_date_iso,
                    d.title AS document_title,
                    dv.original_filename,
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
                JOIN document_versions dv ON dv.id = d.document_version_id
                JOIN sources s ON s.id = d.source_id
                WHERE normalize_text(ar.person_display_name) = normalize_text(?)
                   OR normalize_text(ar.person_name_normalized) = normalize_text(?)
                ORDER BY COALESCE(d.document_date_iso, '') DESC, ar.document_id DESC, ar.order_number ASC, ar.id ASC
                """,
                [normalized_name, normalized_name],
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

                        c.denomination AS center_catalog_name,
                        c.regime AS center_regime,
                        c.full_address AS center_full_address,
                        c.postal_code AS center_postal_code,
                        c.comarca AS center_comarca,
                        c.phone AS center_phone,
                        c.fax AS center_fax,
                        c.latitude AS center_latitude,
                        c.longitude AS center_longitude,

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
                    LEFT JOIN centers c ON c.center_code = aa.center_code
                    WHERE aa.award_result_id IN ({placeholders})
                    ORDER BY aa.award_result_id ASC, aa.id ASC
                    """,
                    award_ids,
                ).fetchall()
            )

            assignment_rows = [
                enrich_center_geo_fields(row, origin_lat, origin_lon)
                for row in assignment_rows
            ]

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
                    d.id AS document_id,
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
                    dc.assigned_position_code,

                    c.denomination AS center_catalog_name,
                    c.regime AS center_regime,
                    c.full_address AS center_full_address,
                    c.postal_code AS center_postal_code,
                    c.comarca AS center_comarca,
                    c.phone AS center_phone,
                    c.fax AS center_fax,
                    c.latitude AS center_latitude,
                    c.longitude AS center_longitude
                FROM difficult_coverage_candidates dc
                JOIN difficult_coverage_positions p ON p.id = dc.position_id
                JOIN documents d ON d.id = p.document_id
                JOIN sources s ON s.id = d.source_id
                LEFT JOIN centers c ON c.center_code = p.center_code
                WHERE normalize_text(dc.full_name) = normalize_text(?)
                   OR normalize_text(dc.full_name_normalized) = normalize_text(?)
                ORDER BY COALESCE(d.document_date_iso, '') DESC, dc.is_selected DESC, dc.row_number ASC, dc.id ASC
                """,
                [normalized_name, normalized_name],
            ).fetchall()
        )

        difficult_coverage = [
            enrich_center_geo_fields(row, origin_lat, origin_lon)
            for row in difficult_coverage
        ]

        bag_memberships = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    dbm.id,
                    dbm.snapshot_id,
                    d.id AS document_id,
                    d.document_date_iso,
                    d.title AS document_title,
                    s.source_key,
                    dbs.course_year,
                    dbs.list_stage,
                    dbs.staff_kind,
                    dbs.position_scope,
                    dbs.list_scope,
                    dbs.body_code,
                    dbs.body_name,
                    dbs.specialty_code,
                    dbs.specialty_name,
                    dbm.order_number,
                    dbm.person_display_name,
                    dbm.service_status,
                    dbm.collective,
                    dbm.habilitations,
                    dbm.disabled_habilitation,
                    dbs.document_url,
                    dbs.source_page_url
                FROM docent_bag_members dbm
                JOIN docent_bag_snapshots dbs ON dbs.id = dbm.snapshot_id
                JOIN documents d ON d.id = dbs.document_id
                JOIN sources s ON s.id = d.source_id
                WHERE normalize_text(dbm.person_display_name) = normalize_text(?)
                   OR normalize_text(dbm.person_name_normalized) = normalize_text(?)
                ORDER BY
                    COALESCE(d.document_date_iso, '') DESC,
                    COALESCE(dbs.course_year, '') DESC,
                    dbs.staff_kind ASC,
                    dbs.list_scope ASC,
                    dbs.specialty_code ASC NULLS FIRST,
                    dbm.order_number ASC,
                    dbm.id ASC
                """,
                [normalized_name, normalized_name],
            ).fetchall()
        )

        enrich_document_provenance(conn, awards)
        enrich_document_provenance(conn, difficult_coverage)
        enrich_document_provenance(conn, bag_memberships)

    user_view = build_user_view(dict(person), awards, difficult_coverage, bag_memberships)

    return {
        "person": {
            "normalized_name": person["normalized_name"],
            "display_name": person["display_name"],
        },
        "user_view": user_view,
        "summary": summary_dict,
        "awards": awards,
        "difficult_coverage": difficult_coverage,
        "bag_memberships": bag_memberships,
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
        enrich_document_provenance(conn, items)

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
        award = dict(award)
        enrich_document_provenance(conn, [award])

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
    specialty_name: str | None = None,
    province: str | None = None,
    locality: str | None = None,
    center_code: str | None = None,
    position_code: str | None = None,
    position_type: str | None = None,
    only_unmatched: bool | None = Query(None, description="True = puestos que no aparecen enlazados desde award_assignments"),
    include_unavailable: bool = Query(False, description="True = incluir plazas adjudicadas o no disponibles"),
    origin_lat: float | None = Query(None, description="Latitud opcional del origen para calcular distancia"),
    origin_lon: float | None = Query(None, description="Longitud opcional del origen para calcular distancia"),
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
    if specialty_name:
        where.append("normalize_text(op.specialty_name) = normalize_text(?)")
        params.append(specialty_name)
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
    if not include_unavailable:
        where.append("1=0" if RECOVERY_READ_ONLY else "op.availability_status = 'available' AND COALESCE(to_jsonb(d)->>'historical_status', 'unknown') = 'accredited'")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_params: list[Any] = []
    if order_by == "distance" and origin_lat is not None and origin_lon is not None:
        order_sql = build_distance_order_by(
            order_dir,
            "d.document_date_iso DESC, op.id ASC",
        )
        order_params = [origin_lat, origin_lat, origin_lon]
    else:
        order_sql = build_order_by(
            order_by,
            order_dir,
            ALLOWED_OFFERED_ORDER_FIELDS,
            "d.document_date_iso",
        )
    order_sql += ", op.id ASC"
    base_sql = f"""
        FROM offered_positions op
        JOIN documents d ON d.id = op.document_id
        JOIN sources s ON s.id = d.source_id
        LEFT JOIN centers c ON c.center_code = op.center_code
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
            op.availability_status,
            op.availability_reason,
            op.closed_by_document_id,
            op.closed_at,

            c.denomination AS center_catalog_name,
            c.regime AS center_regime,
            c.full_address AS center_full_address,
            c.postal_code AS center_postal_code,
            c.comarca AS center_comarca,
            c.phone AS center_phone,
            c.fax AS center_fax,
            c.latitude AS center_latitude,
            c.longitude AS center_longitude,

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
        items = rows_to_dicts(
            conn.execute(items_sql, [*params, *order_params, limit, offset]).fetchall()
        )
        enrich_document_provenance(conn, items)
        mask_unaccredited_availability(items)
    items = [enrich_center_geo_fields(item, origin_lat, origin_lon) for item in items]

    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------- difficult coverage ----------

@app.get("/api/difficult-coverage/positions")
def list_difficult_positions(
    document_id: int | None = None,
    document_date: str | None = None,
    body_code: str | None = None,
    specialty_code: str | None = None,
    specialty_name: str | None = None,
    locality: str | None = None,
    center_code: str | None = None,
    position_code: str | None = None,
    selected_only: bool | None = Query(None, description="True = solo puestos con al menos una persona seleccionada"),
    include_unavailable: bool = Query(False, description="True = incluir plazas adjudicadas o no disponibles"),
    origin_lat: float | None = Query(None, description="Latitud opcional del origen para calcular distancia"),
    origin_lon: float | None = Query(None, description="Longitud opcional del origen para calcular distancia"),
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
    if specialty_name:
        where.append("normalize_text(p.specialty_name) = normalize_text(?)")
        params.append(specialty_name)
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
        where.append("EXISTS (SELECT 1 FROM difficult_coverage_candidates dc WHERE dc.position_id = p.id AND dc.is_selected IS TRUE)")
    elif selected_only is False:
        where.append("NOT EXISTS (SELECT 1 FROM difficult_coverage_candidates dc WHERE dc.position_id = p.id AND dc.is_selected IS TRUE)")
    if not include_unavailable:
        where.append("1=0" if RECOVERY_READ_ONLY else "p.availability_status = 'available' AND COALESCE(to_jsonb(d)->>'historical_status', 'unknown') = 'accredited'")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_params: list[Any] = []
    if order_by == "distance" and origin_lat is not None and origin_lon is not None:
        order_sql = build_distance_order_by(
            order_dir,
            "d.document_date_iso DESC, p.id ASC",
        )
        order_params = [origin_lat, origin_lat, origin_lon]
    else:
        order_sql = build_order_by(
            order_by,
            order_dir,
            ALLOWED_DIFFICULT_ORDER_FIELDS,
            "d.document_date_iso",
        )
    order_sql += ", p.id ASC"

    base_sql = f"""
        FROM difficult_coverage_positions p
        JOIN documents d ON d.id = p.document_id
        JOIN sources s ON s.id = d.source_id
        LEFT JOIN centers c ON c.center_code = p.center_code
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
            p.availability_status,
            p.availability_reason,
            p.closed_by_document_id,
            p.closed_at,

            c.denomination AS center_catalog_name,
            c.regime AS center_regime,
            c.full_address AS center_full_address,
            c.postal_code AS center_postal_code,
            c.comarca AS center_comarca,
            c.phone AS center_phone,
            c.fax AS center_fax,
            c.latitude AS center_latitude,
            c.longitude AS center_longitude,

            (
                SELECT COUNT(*)
                FROM difficult_coverage_candidates dc
                WHERE dc.position_id = p.id
            ) AS candidate_count,
            (
                SELECT COUNT(*)
                FROM difficult_coverage_candidates dc
                WHERE dc.position_id = p.id AND dc.is_selected IS TRUE
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
        items = rows_to_dicts(conn.execute(items_sql, [*params, *order_params, limit, offset]).fetchall())
        enrich_document_provenance(conn, items)
        mask_unaccredited_availability(items)

    items = [enrich_center_geo_fields(item, origin_lat, origin_lon) for item in items]

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
        position = dict(position)
        enrich_document_provenance(conn, [position])

        where = ["dc.position_id = ?"]
        params: list[Any] = [position_id]
        if selected_only is True:
            where.append("dc.is_selected IS TRUE")
        elif selected_only is False:
            where.append("dc.is_selected IS FALSE")
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


# ---------- non-docent educational support staff ----------

ALLOWED_NON_DOCENT_PUBLICATION_ORDER_FIELDS = {
    "publication_date": "p.publication_date_iso",
    "created_at": "p.created_at",
    "title": "p.title",
    "id": "p.id",
}

ALLOWED_NON_DOCENT_POSITION_ORDER_FIELDS = {
    "publication_date": "p.publication_date_iso",
    "locality": "pos.locality",
    "center_name": "pos.center_name",
    "position_code": "pos.position_code",
    "id": "pos.id",
}

ALLOWED_NON_DOCENT_AWARD_ORDER_FIELDS = {
    "publication_date": "p.publication_date_iso",
    "person_name": "aw.person_display_name",
    "score": "aw.score",
    "id": "aw.id",
}

ALLOWED_NON_DOCENT_BAG_ORDER_FIELDS = {
    "snapshot_date": "bs.snapshot_date_iso",
    "order_number": "bm.order_number",
    "person_name": "bm.person_display_name",
    "total_score": "bm.total_score",
    "id": "bm.id",
}


def _add_non_docent_staff_group_filter(
    where: list[str],
    params: list[Any],
    table_alias: str,
    staff_group_code: str | None,
) -> None:
    if staff_group_code:
        where.append(f"{table_alias}.code = ?")
        params.append(staff_group_code.upper())


@app.get("/api/non-docent/summary")
def get_non_docent_summary() -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        totals = dict(
            conn.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM non_docent_publications) AS publications,
                    (SELECT COUNT(*) FROM non_docent_offered_positions) AS offered_positions,
                    (SELECT COUNT(*) FROM non_docent_awards) AS awards,
                    (SELECT COUNT(*) FROM non_docent_bag_snapshots) AS bag_snapshots,
                    (SELECT COUNT(*) FROM non_docent_bag_members) AS bag_members
                """
            ).fetchone()
        )
        # Keep this summary query cheap: do not join all child tables at once.
        # Joining publications, positions, awards, snapshots and bag members multiplies
        # rows and forces PostgreSQL to build large temporary DISTINCT sets.
        # Correlated aggregate subqueries are fast here because staff groups are few
        # and the related foreign-key columns are indexed.
        by_group = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    g.code AS staff_group_code,
                    g.name AS staff_group_name,
                    g.administration_scope,
                    (
                        SELECT COUNT(*)
                        FROM non_docent_publications p
                        WHERE p.staff_group_id = g.id
                    ) AS publications_count,
                    (
                        SELECT COUNT(*)
                        FROM non_docent_offered_positions pos
                        WHERE pos.staff_group_id = g.id
                    ) AS offered_positions_count,
                    (
                        SELECT COUNT(*)
                        FROM non_docent_awards aw
                        WHERE aw.staff_group_id = g.id
                    ) AS awards_count,
                    (
                        SELECT COUNT(*)
                        FROM non_docent_bag_snapshots bs
                        WHERE bs.staff_group_id = g.id
                    ) AS bag_snapshots_count,
                    (
                        SELECT COUNT(*)
                        FROM non_docent_bag_members bm
                        JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
                        WHERE bs.staff_group_id = g.id
                    ) AS bag_members_count,
                    (
                        SELECT MAX(p.publication_date_iso)
                        FROM non_docent_publications p
                        WHERE p.staff_group_id = g.id
                    ) AS latest_publication_date
                FROM non_docent_staff_groups g
                WHERE g.is_active IS TRUE
                ORDER BY g.code ASC
                """
            ).fetchall()
        )
        latest_publications = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    p.id,
                    p.document_id,
                    p.publication_kind,
                    p.publication_code,
                    p.title,
                    p.publication_date_text,
                    p.publication_date_iso,
                    p.source_page_url,
                    p.document_url,
                    g.code AS staff_group_code,
                    g.name AS staff_group_name
                FROM non_docent_publications p
                LEFT JOIN non_docent_staff_groups g ON g.id = p.staff_group_id
                ORDER BY COALESCE(p.publication_date_iso, '') DESC, p.id DESC
                LIMIT 10
                """
            ).fetchall()
        )
    return {
        "totals": totals,
        "by_group": by_group,
        "latest_publications": latest_publications,
    }


@app.get("/api/non-docent/publications")
def list_non_docent_publications(
    staff_group_code: str | None = None,
    publication_kind: str | None = None,
    q: str | None = Query(None, description="Texto libre en título, código o URL"),
    from_date: str | None = Query(None, description="YYYY-MM-DD"),
    to_date: str | None = Query(None, description="YYYY-MM-DD"),
    order_by: str = Query("publication_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    _add_non_docent_staff_group_filter(where, params, "g", staff_group_code)
    if publication_kind:
        where.append("p.publication_kind = ?")
        params.append(publication_kind)
    if q:
        where.append(
            "(normalize_text(p.title) LIKE normalize_text(?) OR normalize_text(COALESCE(p.publication_code, '')) LIKE normalize_text(?) OR normalize_text(COALESCE(p.document_url, '')) LIKE normalize_text(?))"
        )
        pattern = f"%{q}%"
        params.extend([pattern, pattern, pattern])
    if from_date:
        where.append("p.publication_date_iso >= ?")
        params.append(from_date)
    if to_date:
        where.append("p.publication_date_iso <= ?")
        params.append(to_date)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(
        order_by,
        order_dir,
        ALLOWED_NON_DOCENT_PUBLICATION_ORDER_FIELDS,
        "p.publication_date_iso",
    )
    base_sql = f"""
        FROM non_docent_publications p
        LEFT JOIN non_docent_staff_groups g ON g.id = p.staff_group_id
        LEFT JOIN documents d ON d.id = p.document_id
        {where_sql}
    """
    items_sql = (
        """
        SELECT
            p.id,
            p.document_id,
            d.doc_family,
            p.publication_kind,
            p.publication_code,
            p.title,
            p.source_page_url,
            p.document_url,
            p.publication_date_text,
            p.publication_date_iso,
            p.status_text,
            p.is_current,
            p.missing_since,
            p.availability_status,
            p.availability_reason,
            p.closed_by_document_id,
            p.closed_at,
            p.notes,
            g.code AS staff_group_code,
            g.name AS staff_group_name,
            g.administration_scope,
            (SELECT COUNT(*) FROM non_docent_offered_positions pos WHERE pos.publication_id = p.id) AS positions_count,
            (SELECT COUNT(*) FROM non_docent_awards aw WHERE aw.publication_id = p.id) AS awards_count,
            (
                SELECT COUNT(*)
                FROM non_docent_bag_members bm
                JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
                WHERE bs.publication_id = p.id
            ) AS bag_members_count
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

    if RECOVERY_READ_ONLY:
        for item in items:
            if item.get('availability_status') == 'available':
                item['availability_status'] = 'unknown'
                item['availability_reason'] = 'vigencia_no_acreditada'
            if item.get('is_current') is True:
                item['is_current'] = None
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/non-docent/positions")
def list_non_docent_positions(
    staff_group_code: str | None = None,
    publication_id: int | None = None,
    publication_code: str | None = None,
    province: str | None = None,
    locality: str | None = None,
    position_code: str | None = None,
    q: str | None = Query(None, description="Texto libre en denominación, centro o localidad"),
    include_unavailable: bool = Query(False, description="True = incluir plazas adjudicadas o no disponibles"),
    order_by: str = Query("publication_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    _add_non_docent_staff_group_filter(where, params, "g", staff_group_code)
    if publication_id:
        where.append("pos.publication_id = ?")
        params.append(publication_id)
    if publication_code:
        where.append("p.publication_code = ?")
        params.append(publication_code)
    if province:
        where.append("normalize_text(pos.province) = normalize_text(?)")
        params.append(province)
    if locality:
        where.append("normalize_text(pos.locality) LIKE normalize_text(?)")
        params.append(f"%{locality}%")
    if position_code:
        where.append("pos.position_code = ?")
        params.append(position_code)
    if q:
        where.append(
            "(normalize_text(COALESCE(pos.denomination, '')) LIKE normalize_text(?) OR normalize_text(COALESCE(pos.center_name, '')) LIKE normalize_text(?) OR normalize_text(COALESCE(pos.locality, '')) LIKE normalize_text(?))"
        )
        pattern = f"%{q}%"
        params.extend([pattern, pattern, pattern])
    if not include_unavailable:
        # Legacy non-docent rows have no accredited historical-vigency field.
        # In recovery, the old schema default alone cannot establish availability.
        where.append("1=0" if RECOVERY_READ_ONLY else "pos.availability_status = 'available'")

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(
        order_by,
        order_dir,
        ALLOWED_NON_DOCENT_POSITION_ORDER_FIELDS,
        "p.publication_date_iso",
    )
    order_sql += ", pos.id ASC"
    base_sql = f"""
        FROM non_docent_offered_positions pos
        JOIN non_docent_publications p ON p.id = pos.publication_id
        LEFT JOIN non_docent_staff_groups g ON g.id = pos.staff_group_id
        {where_sql}
    """
    items_sql = (
        """
        SELECT
            pos.id,
            pos.publication_id,
            p.document_id,
            p.publication_kind,
            p.publication_code,
            p.publication_date_text,
            p.publication_date_iso,
            p.title AS publication_title,
            p.document_url,
            g.code AS staff_group_code,
            g.name AS staff_group_name,
            pos.position_code,
            pos.classification,
            pos.denomination,
            pos.center_name,
            pos.center_code,
            pos.locality,
            pos.province,
            pos.occupancy_percent,
            pos.functional_assignment,
            pos.reason,
            pos.availability_status,
            pos.availability_reason,
            pos.closed_by_document_id,
            pos.closed_at,
            pos.raw_row_text
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

    if RECOVERY_READ_ONLY:
        for item in items:
            if item.get('availability_status') == 'available':
                item['availability_status'] = 'unknown'
                item['availability_reason'] = 'vigencia_no_acreditada'
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@app.get("/api/non-docent/awards")
def list_non_docent_awards(
    staff_group_code: str | None = None,
    publication_id: int | None = None,
    publication_code: str | None = None,
    q: str | None = Query(None, description="Texto libre sobre nombre de persona"),
    position_code: str | None = None,
    bag_code: str | None = None,
    order_by: str = Query("publication_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    where: list[str] = []
    params: list[Any] = []

    _add_non_docent_staff_group_filter(where, params, "g", staff_group_code)
    if publication_id:
        where.append("aw.publication_id = ?")
        params.append(publication_id)
    if publication_code:
        where.append("p.publication_code = ?")
        params.append(publication_code)
    if q:
        where.append("(normalize_text(aw.person_display_name) LIKE normalize_text(?) OR aw.person_name_normalized LIKE normalize_text(?))")
        pattern = f"%{q}%"
        params.extend([pattern, pattern])
    if position_code:
        where.append("aw.position_code = ?")
        params.append(position_code)
    if bag_code:
        where.append("aw.bag_code = ?")
        params.append(bag_code)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = build_order_by(
        order_by,
        order_dir,
        ALLOWED_NON_DOCENT_AWARD_ORDER_FIELDS,
        "p.publication_date_iso",
    )
    base_sql = f"""
        FROM non_docent_awards aw
        JOIN non_docent_publications p ON p.id = aw.publication_id
        LEFT JOIN non_docent_staff_groups g ON g.id = aw.staff_group_id
        {where_sql}
    """
    items_sql = (
        """
        SELECT
            aw.id,
            aw.publication_id,
            p.document_id,
            p.publication_kind,
            p.publication_code,
            p.publication_date_text,
            p.publication_date_iso,
            p.title AS publication_title,
            p.document_url,
            g.code AS staff_group_code,
            g.name AS staff_group_name,
            aw.bag_code,
            aw.bag_name,
            aw.score,
            aw.scope_text,
            aw.person_display_name,
            aw.person_name_normalized,
            aw.career_official_text,
            aw.position_code,
            aw.position_text,
            aw.locality,
            aw.center_name,
            aw.is_deserted,
            aw.raw_row_text
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


@app.get("/api/non-docent/bag-members/search")
def search_non_docent_bag_members(
    q: str = Query(..., min_length=2, description="Nombre, apellidos o DNI anonimizado"),
    staff_group_code: str | None = None,
    bag_code: str | None = None,
    source_kind: str | None = None,
    status: str | None = Query(None, description="Disponible, Actiu, No disponible..., etc."),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    order_by: str = Query("snapshot_date"),
    order_dir: str = Query("desc", pattern="^(asc|desc)$"),
) -> dict[str, Any]:
    where: list[str] = [
        "(normalize_text(bm.person_display_name) LIKE normalize_text(?) OR bm.person_name_normalized LIKE normalize_text(?) OR bm.masked_dni LIKE ?)"
    ]
    pattern = f"%{q}%"
    params: list[Any] = [pattern, pattern, pattern]

    _add_non_docent_staff_group_filter(where, params, "g", staff_group_code)
    if bag_code:
        where.append("bs.bag_code = ?")
        params.append(bag_code)
    if source_kind:
        where.append("bs.source_kind = ?")
        params.append(source_kind)
    if status:
        where.append("normalize_text(COALESCE(bm.status_text, '')) LIKE normalize_text(?)")
        params.append(f"%{status}%")

    where_sql = f"WHERE {' AND '.join(where)}"
    order_sql = build_order_by(
        order_by,
        order_dir,
        ALLOWED_NON_DOCENT_BAG_ORDER_FIELDS,
        "bs.snapshot_date_iso",
    )
    base_sql = f"""
        FROM non_docent_bag_members bm
        JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
        JOIN non_docent_publications p ON p.id = bs.publication_id
        LEFT JOIN non_docent_staff_groups g ON g.id = bs.staff_group_id
        {where_sql}
    """
    items_sql = (
        """
        SELECT
            bm.id,
            bm.snapshot_id,
            bs.publication_id,
            p.document_id,
            p.publication_kind,
            p.publication_code,
            p.title AS publication_title,
            p.document_url,
            bs.bag_code,
            bs.bag_name,
            bs.source_kind,
            bs.snapshot_date_text,
            bs.snapshot_date_iso,
            bs.zone_text,
            g.code AS staff_group_code,
            g.name AS staff_group_name,
            bm.order_number,
            bm.masked_dni,
            bm.person_display_name,
            bm.person_name_normalized,
            bm.total_score,
            bm.status_text,
            bm.annotation_text,
            bm.start_date_text,
            bm.end_date_text
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

    return {"items": items, "total": total, "limit": limit, "offset": offset, "query": q}


@app.get("/api/non-docent/persons/search")
def search_non_docent_persons(
    q: str = Query(..., min_length=2),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    pattern = f"%{q}%"
    with get_connection() as conn:
        _register_normalize_function(conn)
        items = rows_to_dicts(
            conn.execute(
                """
                WITH award_people AS (
                    SELECT
                        aw.person_name_normalized AS normalized_name,
                        aw.person_display_name AS display_name,
                        COUNT(*) AS total_awards,
                        0::bigint AS total_bag_records,
                        MAX(p.publication_date_iso) AS last_seen_date
                    FROM non_docent_awards aw
                    JOIN non_docent_publications p ON p.id = aw.publication_id
                    WHERE normalize_text(aw.person_display_name) LIKE normalize_text(?)
                       OR aw.person_name_normalized LIKE normalize_text(?)
                    GROUP BY aw.person_name_normalized, aw.person_display_name
                ),
                bag_people AS (
                    SELECT
                        bm.person_name_normalized AS normalized_name,
                        bm.person_display_name AS display_name,
                        0::bigint AS total_awards,
                        COUNT(*) AS total_bag_records,
                        MAX(bs.snapshot_date_iso) AS last_seen_date
                    FROM non_docent_bag_members bm
                    JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
                    WHERE normalize_text(bm.person_display_name) LIKE normalize_text(?)
                       OR bm.person_name_normalized LIKE normalize_text(?)
                    GROUP BY bm.person_name_normalized, bm.person_display_name
                ),
                unioned AS (
                    SELECT * FROM award_people
                    UNION ALL
                    SELECT * FROM bag_people
                )
                SELECT
                    normalized_name,
                    display_name,
                    SUM(total_awards) AS total_awards,
                    SUM(total_bag_records) AS total_bag_records,
                    MAX(last_seen_date) AS last_seen_date
                FROM unioned
                GROUP BY normalized_name, display_name
                ORDER BY (SUM(total_awards) + SUM(total_bag_records)) DESC, display_name ASC
                LIMIT ?
                """,
                [pattern, pattern, pattern, pattern, limit],
            ).fetchall()
        )
    return {"items": items, "count": len(items), "query": q}


@app.get("/api/non-docent/persons/profile")
def get_non_docent_person_profile(
    normalized_name: str = Query(..., min_length=2),
    limit_bag_records: int = Query(100, ge=1, le=500),
) -> dict[str, Any]:
    with get_connection() as conn:
        _register_normalize_function(conn)
        person = conn.execute(
            """
            WITH candidates AS (
                SELECT
                    aw.person_name_normalized AS normalized_name,
                    aw.person_display_name AS display_name,
                    COUNT(*) AS total_rows
                FROM non_docent_awards aw
                WHERE aw.person_name_normalized = ?
                GROUP BY aw.person_name_normalized, aw.person_display_name

                UNION ALL

                SELECT
                    bm.person_name_normalized AS normalized_name,
                    bm.person_display_name AS display_name,
                    COUNT(*) AS total_rows
                FROM non_docent_bag_members bm
                WHERE bm.person_name_normalized = ?
                GROUP BY bm.person_name_normalized, bm.person_display_name
            )
            SELECT normalized_name, display_name
            FROM candidates
            ORDER BY total_rows DESC, display_name ASC
            LIMIT 1
            """,
            [normalized_name, normalized_name],
        ).fetchone()

        if person is None:
            raise HTTPException(status_code=404, detail="normalized_name no encontrado")

        summary = dict(
            conn.execute(
                """
                WITH award_summary AS (
                    SELECT
                        COUNT(*) AS total_awards,
                        MAX(p.publication_date_iso) AS last_award_date
                    FROM non_docent_awards aw
                    JOIN non_docent_publications p ON p.id = aw.publication_id
                    WHERE aw.person_name_normalized = ?
                ),
                bag_summary AS (
                    SELECT
                        COUNT(*) AS total_bag_records,
                        MAX(bs.snapshot_date_iso) AS last_bag_date
                    FROM non_docent_bag_members bm
                    JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
                    WHERE bm.person_name_normalized = ?
                )
                SELECT
                    COALESCE(award_summary.total_awards, 0) AS total_awards,
                    COALESCE(bag_summary.total_bag_records, 0) AS total_bag_records,
                    award_summary.last_award_date,
                    bag_summary.last_bag_date,
                    CASE
                        WHEN award_summary.last_award_date IS NULL THEN bag_summary.last_bag_date
                        WHEN bag_summary.last_bag_date IS NULL THEN award_summary.last_award_date
                        WHEN award_summary.last_award_date >= bag_summary.last_bag_date THEN award_summary.last_award_date
                        ELSE bag_summary.last_bag_date
                    END AS last_seen_date
                FROM award_summary, bag_summary
                """,
                [normalized_name, normalized_name],
            ).fetchone()
        )

        awards = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    aw.id,
                    aw.publication_id,
                    p.document_id,
                    p.publication_kind,
                    p.publication_code,
                    p.publication_date_text,
                    p.publication_date_iso,
                    p.title AS publication_title,
                    p.document_url,
                    g.code AS staff_group_code,
                    g.name AS staff_group_name,
                    aw.bag_code,
                    aw.bag_name,
                    aw.score,
                    aw.scope_text,
                    aw.career_official_text,
                    aw.position_code,
                    aw.position_text,
                    aw.locality,
                    aw.center_name,
                    aw.is_deserted
                FROM non_docent_awards aw
                JOIN non_docent_publications p ON p.id = aw.publication_id
                LEFT JOIN non_docent_staff_groups g ON g.id = aw.staff_group_id
                WHERE aw.person_name_normalized = ?
                ORDER BY COALESCE(p.publication_date_iso, '') DESC, aw.id DESC
                """,
                [normalized_name],
            ).fetchall()
        )

        bag_records = rows_to_dicts(
            conn.execute(
                """
                SELECT
                    bm.id,
                    bm.snapshot_id,
                    bs.publication_id,
                    p.document_id,
                    p.publication_kind,
                    p.publication_code,
                    p.title AS publication_title,
                    p.document_url,
                    bs.bag_code,
                    bs.bag_name,
                    bs.source_kind,
                    bs.snapshot_date_text,
                    bs.snapshot_date_iso,
                    bs.zone_text,
                    g.code AS staff_group_code,
                    g.name AS staff_group_name,
                    bm.order_number,
                    bm.masked_dni,
                    bm.total_score,
                    bm.status_text,
                    bm.annotation_text,
                    bm.start_date_text,
                    bm.end_date_text
                FROM non_docent_bag_members bm
                JOIN non_docent_bag_snapshots bs ON bs.id = bm.snapshot_id
                JOIN non_docent_publications p ON p.id = bs.publication_id
                LEFT JOIN non_docent_staff_groups g ON g.id = bs.staff_group_id
                WHERE bm.person_name_normalized = ?
                ORDER BY COALESCE(bs.snapshot_date_iso, '') DESC, bs.bag_code ASC, bm.order_number ASC, bm.id ASC
                LIMIT ?
                """,
                [normalized_name, limit_bag_records],
            ).fetchall()
        )

    latest_award = awards[0] if awards else None
    latest_bag_record = bag_records[0] if bag_records else None
    user_view = {
        "display_name": person["display_name"],
        "current_result": "no_data",
        "current_result_label": "Sin datos interpretables",
        "current_result_message": "No hay información suficiente para mostrar un resumen claro.",
        "latest_date": summary.get("last_seen_date"),
        "latest_staff_group_label": None,
        "latest_bag_status": None,
        "latest_awarded_position": None,
        "recommended_action": "Consulta siempre la publicación oficial enlazada para confirmar tu situación administrativa.",
    }

    if latest_award:
        user_view.update(
            {
                "current_result": "non_docent_awarded",
                "current_result_label": "Adjudicación localizada",
                "current_result_message": "Consta una adjudicación no docente en los datos cargados.",
                "latest_date": latest_award.get("publication_date_iso"),
                "latest_staff_group_label": latest_award.get("staff_group_name"),
                "latest_awarded_position": latest_award.get("position_text"),
            }
        )
    elif latest_bag_record:
        user_view.update(
            {
                "current_result": "non_docent_bag_member",
                "current_result_label": "Consta en bolsa",
                "current_result_message": "Consta una posición en bolsa no docente en los datos cargados.",
                "latest_date": latest_bag_record.get("snapshot_date_iso"),
                "latest_staff_group_label": latest_bag_record.get("staff_group_name"),
                "latest_bag_status": latest_bag_record.get("status_text"),
            }
        )

    return {
        "person": {
            "normalized_name": person["normalized_name"],
            "display_name": person["display_name"],
        },
        "summary": summary,
        "user_view": user_view,
        "awards": awards,
        "bag_records": bag_records,
    }

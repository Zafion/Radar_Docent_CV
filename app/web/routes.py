from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(include_in_schema=False)

OFFICIAL_SECTION_URL = "https://ceice.gva.es/es/web/rrhh-educacion"
OFFICIAL_RESOLUCION_URL = "https://ceice.gva.es/es/web/rrhh-educacion/resolucion"
OFFICIAL_ADJUDICACIONES_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicaciones"


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "active_page": "home",
            "page_title": "funcionario.com | Inicio",
        },
    )


@router.get("/valencia-docentes", response_class=HTMLResponse)
def valencia_docentes(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="valencia_docentes.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": "funcionario.com | Consulta de Plazas y Adjudicaciones Docentes",
            "official_section_url": OFFICIAL_SECTION_URL,
            "official_resolucion_url": OFFICIAL_RESOLUCION_URL,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
        },
    )


@router.get("/quienes-somos", response_class=HTMLResponse)
def quienes_somos(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="quienes_somos.html",
        context={
            "active_page": "quienes-somos",
            "page_title": "funcionario.com | Quiénes Somos",
        },
    )


@router.get("/contacto", response_class=HTMLResponse)
def contacto(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="contacto.html",
        context={
            "active_page": "contacto",
            "page_title": "funcionario.com | Contacto",
        },
    )

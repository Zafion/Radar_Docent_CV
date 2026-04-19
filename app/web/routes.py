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
PROJECT_EMAIL = "funkcionarios@gmail.com"
PROJECT_OWNER = "Jose Luis Montañana Llopis"
PROJECT_LINKEDIN = "https://www.linkedin.com/in/jose-luis-monta%C3%B1ana-llopis-116941172/?lipi=urn%3Ali%3Apage%3Ad_flagship3_feed%3BtjegxX7vR4msI4sRX5YxCQ%3D%3D"


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="home.html",
        context={
            "active_page": "home",
            "page_title": "funkcionario.com | Inicio",
        },
    )


@router.get("/valencia-docentes", response_class=HTMLResponse)
def valencia_docentes(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="valencia_docentes.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": "funkcionario.com | Consulta de Plazas y Adjudicaciones Docentes",
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
            "page_title": "funkcionario.com | Quiénes Somos",
            "project_owner": PROJECT_OWNER,
        },
    )


@router.get("/contacto", response_class=HTMLResponse)
def contacto(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="contacto.html",
        context={
            "active_page": "contacto",
            "page_title": "funkcionario.com | Contacto",
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
            "project_linkedin": PROJECT_LINKEDIN,
        },
    )

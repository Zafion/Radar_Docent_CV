from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(include_in_schema=False)

OFFICIAL_SECTION_URL = "https://ceice.gva.es/es/web/rrhh-educacion"
OFFICIAL_RESOLUCION_URL = "https://ceice.gva.es/es/web/rrhh-educacion/resolucion"
OFFICIAL_ADJUDICACIONES_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicaciones"
OFFICIAL_ADJUDICACIONES_CONTINUAS_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicaciones-continuas"
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


@router.get("/plazas-ofertadas", response_class=HTMLResponse)
def offered_positions(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="offered_positions.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": "funkcionario.com | Plazas Ofertadas",
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
        },
    )


@router.get("/resultado-persona", response_class=HTMLResponse)
def person_detail(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="person_detail.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": "funkcionario.com | Resultado por persona",
            "official_resolucion_url": OFFICIAL_RESOLUCION_URL,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
            "official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL,
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


@router.get("/centros/{center_code}", response_class=HTMLResponse)
def center_detail(request: Request, center_code: str):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="center_detail.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": f"funkcionario.com | Centro {center_code}",
            "center_code": center_code,
        },
    )


@router.get("/adjudicaciones/{award_result_id}", response_class=HTMLResponse)
def award_detail(request: Request, award_result_id: int):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="award_detail.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": f"funkcionario.com | Adjudicación {award_result_id}",
            "award_result_id": award_result_id,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
        },
    )


@router.get("/dificil-cobertura", response_class=HTMLResponse)
def difficult_coverage(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="difficult_coverage.html",
        context={
            "active_page": "valencia-docentes",
            "page_title": "funkcionario.com | Difícil Cobertura",
            "official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL,
        },
    )


@router.get("/404", response_class=HTMLResponse)
def custom_404_preview(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="404.html",
        context={
            "active_page": "not-found",
            "page_title": "funkcionario.com | Funk not found",
        },
        status_code=404,
    )


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(BASE_DIR / "static" / "js" / "sw.js", media_type="application/javascript")


@router.get("/politica-privacidad", response_class=HTMLResponse)
def politica_privacidad(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="politica_privacidad.html",
        context={
            "active_page": "legal",
            "page_title": "funkcionario.com | Política de Privacidad",
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
        },
    )


@router.get("/politica-cookies", response_class=HTMLResponse)
def politica_cookies(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="politica_cookies.html",
        context={
            "active_page": "legal",
            "page_title": "funkcionario.com | Política de Cookies",
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
        },
    )

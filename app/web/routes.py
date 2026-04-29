from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.templating import Jinja2Templates

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(include_in_schema=False)

OFFICIAL_SECTION_URL = "https://ceice.gva.es/es/web/rrhh-educacion"
OFFICIAL_RESOLUCION_URL = "https://ceice.gva.es/es/web/rrhh-educacion/resolucion"
OFFICIAL_ADJUDICACIONES_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicaciones"
OFFICIAL_ADJUDICACIONES_CONTINUAS_URL = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicaciones-continuas"
OFFICIAL_NON_DOCENT_BASE_URL = "https://ceice.gva.es/es/web/inclusioeducativa/personal-no-docent"
OFFICIAL_NON_DOCENT_BAGS_URL = "https://ceice.gva.es/es/web/inclusioeducativa/personal-no-docent/borses-ocupacio-temporal"
PROJECT_EMAIL = "zafion+funkcionario@gmail.com"
PROJECT_OWNER = "Jose Luis Montañana Llopis"
PROJECT_LINKEDIN = "https://www.linkedin.com/in/jose-luis-monta%C3%B1ana-llopis-116941172/?lipi=urn%3Ali%3Apage%3Ad_flagship3_feed%3BtjegxX7vR4msI4sRX5YxCQ%3D%3D"

DEFAULT_DESCRIPTION = (
    "Funkcionario.com ayuda a consultar plazas ofertadas, adjudicaciones docentes "
    "y seguimiento de personal interino docente y no docente educativo en la Comunitat Valenciana."
)

SITEMAP_PAGES: tuple[tuple[str, str, str], ...] = (
    ("/", "1.0", "daily"),
    ("/valencia-docentes", "0.9", "daily"),
    ("/valencia-no-docentes", "0.9", "daily"),
    ("/no-docente/plazas", "0.8", "daily"),
    ("/no-docente/adjudicaciones", "0.8", "daily"),
    ("/no-docente/consulta-persona", "0.8", "daily"),
    ("/plazas-ofertadas", "0.9", "daily"),
    ("/consulta-persona", "0.8", "daily"),
    ("/dificil-cobertura", "0.9", "daily"),
    ("/quienes-somos", "0.5", "monthly"),
    ("/contacto", "0.4", "monthly"),
    ("/politica-privacidad", "0.3", "yearly"),
    ("/politica-cookies", "0.3", "yearly"),
)


def get_public_base_url(request: Request) -> str:
    """Return the public canonical base URL.

    In production set RADAR_PUBLIC_BASE_URL=https://funkcionario.com.
    In local development, when the variable is not set, use the current request base URL
    so the app keeps working at http://127.0.0.1:8000 without forcing the live domain.
    """
    configured = os.getenv("RADAR_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if configured:
        return configured

    return str(request.base_url).rstrip("/")


def absolute_url(request: Request, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{get_public_base_url(request)}{path}"


def build_base_json_ld(request: Request) -> list[dict]:
    base_url = get_public_base_url(request)

    return [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "Funkcionario.com",
            "url": base_url,
            "description": DEFAULT_DESCRIPTION,
            "inLanguage": "es-ES",
        },
        {
            "@context": "https://schema.org",
            "@type": "Organization",
            "name": "Funkcionario.com",
            "url": base_url,
            "email": PROJECT_EMAIL,
            "founder": {
                "@type": "Person",
                "name": PROJECT_OWNER,
            },
        },
    ]


def build_breadcrumb_json_ld(request: Request, items: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": label,
                "item": absolute_url(request, path),
            }
            for index, (label, path) in enumerate(items, start=1)
        ],
    }


def seo_context(
    request: Request,
    *,
    page_title: str,
    page_description: str,
    path: str,
    active_page: str,
    robots_meta: str = "index,follow",
    breadcrumbs: list[tuple[str, str]] | None = None,
    page_type: str = "website",
    extra_json_ld: list[dict] | None = None,
) -> dict:
    json_ld = build_base_json_ld(request)

    if breadcrumbs:
        json_ld.append(build_breadcrumb_json_ld(request, breadcrumbs))

    if extra_json_ld:
        json_ld.extend(extra_json_ld)

    return {
        "active_page": active_page,
        "page_title": page_title,
        "page_description": page_description,
        "canonical_url": absolute_url(request, path),
        "robots_meta": robots_meta,
        "og_type": page_type,
        "og_image_url": absolute_url(request, "/static/img/og-image.png"),
        "site_name": "Funkcionario.com",
        "seo_json_ld": json_ld,
    }


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="home.html",
        context=seo_context(
            request,
            active_page="home",
            page_title="funkcionario.com | Adjudicaciones docentes en la Comunitat Valenciana",
            page_description=(
                "Consulta en Funkcionario.com plazas ofertadas, adjudicaciones docentes, "
                "difícil cobertura y resultados de personal interino docente en la Comunitat Valenciana."
            ),
            path="/",
            breadcrumbs=[("Inicio", "/")],
        ),
    )


@router.get("/valencia-docentes", response_class=HTMLResponse)
def valencia_docentes(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Consulta de plazas y adjudicaciones docentes",
        page_description=(
            "Panel de consulta para acceder a plazas ofertadas, consulta por persona, "
            "adjudicaciones y puestos de difícil cobertura docente en la Comunitat Valenciana."
        ),
        path="/valencia-docentes",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes")],
    )
    context.update(
        {
            "official_section_url": OFFICIAL_SECTION_URL,
            "official_resolucion_url": OFFICIAL_RESOLUCION_URL,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="valencia_docentes.html", context=context)


@router.get("/valencia-no-docentes", response_class=HTMLResponse)
def valencia_no_docentes(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="funkcionario.com | Personal no docente de atención educativa",
        page_description=(
            "Panel de consulta para personal no docente de atención educativa: "
            "plazas ADC, adjudicaciones y bolsas de empleo temporal publicadas por Conselleria."
        ),
        path="/valencia-no-docentes",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes")],
    )
    context.update(
        {
            "official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL,
            "official_non_docent_bags_url": OFFICIAL_NON_DOCENT_BAGS_URL,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="valencia_no_docentes.html", context=context)


@router.get("/no-docente/plazas", response_class=HTMLResponse)
def non_docent_positions(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="funkcionario.com | Plazas no docentes ofertadas",
        page_description=(
            "Consulta plazas ADC ofertadas para personal no docente de atención educativa "
            "en la Comunitat Valenciana."
        ),
        path="/no-docente/plazas",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Plazas", "/no-docente/plazas")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_positions.html", context=context)


@router.get("/no-docente/adjudicaciones", response_class=HTMLResponse)
def non_docent_awards(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="funkcionario.com | Adjudicaciones no docentes",
        page_description=(
            "Consulta adjudicaciones ADC publicadas para personal no docente de atención educativa."
        ),
        path="/no-docente/adjudicaciones",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Adjudicaciones", "/no-docente/adjudicaciones")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_awards.html", context=context)


@router.get("/no-docente/consulta-persona", response_class=HTMLResponse)
def non_docent_person_search(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="funkcionario.com | Consulta no docente por persona",
        page_description=(
            "Busca una persona en adjudicaciones y bolsas no docentes de atención educativa."
        ),
        path="/no-docente/consulta-persona",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Consulta por persona", "/no-docente/consulta-persona")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_person_search.html", context=context)


@router.get("/no-docente/resultado-persona", response_class=HTMLResponse)
def non_docent_person_detail(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="funkcionario.com | Resultado no docente por persona",
        page_description="Resultado individual de consulta no docente por persona en Funkcionario.com.",
        path="/no-docente/resultado-persona",
        robots_meta="noindex,nofollow",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Resultado", "/no-docente/resultado-persona")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_person_detail.html", context=context)


@router.get("/plazas-ofertadas", response_class=HTMLResponse)
def offered_positions(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Plazas ofertadas docentes",
        page_description=(
            "Consulta las últimas plazas docentes ofertadas en la Comunitat Valenciana, "
            "con filtros por fecha, localidad, centro, especialidad y distancia aproximada."
        ),
        path="/plazas-ofertadas",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes"), ("Plazas ofertadas", "/plazas-ofertadas")],
    )
    context.update({"official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL})
    return TEMPLATES.TemplateResponse(request=request, name="offered_positions.html", context=context)


@router.get("/consulta-persona", response_class=HTMLResponse)
def person_search(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Consulta por persona",
        page_description=(
            "Busca coincidencias por nombre para consultar una ficha de adjudicaciones docentes, "
            "participación en procedimientos y difícil cobertura."
        ),
        path="/consulta-persona",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes"), ("Consulta por persona", "/consulta-persona")],
    )
    context.update(
        {
            "official_resolucion_url": OFFICIAL_RESOLUCION_URL,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
            "official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="person_search.html", context=context)


@router.get("/resultado-persona", response_class=HTMLResponse)
def person_detail(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Resultado por persona",
        page_description="Resultado individual de consulta por persona en Funkcionario.com.",
        path="/resultado-persona",
        robots_meta="noindex,nofollow",
        breadcrumbs=[("Inicio", "/"), ("Consulta por persona", "/consulta-persona"), ("Resultado", "/resultado-persona")],
    )
    context.update(
        {
            "official_resolucion_url": OFFICIAL_RESOLUCION_URL,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
            "official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="person_detail.html", context=context)


@router.get("/quienes-somos", response_class=HTMLResponse)
def quienes_somos(request: Request):
    context = seo_context(
        request,
        active_page="quienes-somos",
        page_title="funkcionario.com | Quiénes somos",
        page_description=(
            "Información sobre Funkcionario.com, proyecto de consulta y seguimiento de "
            "adjudicaciones docentes e interinos en la Comunitat Valenciana."
        ),
        path="/quienes-somos",
        breadcrumbs=[("Inicio", "/"), ("Quiénes somos", "/quienes-somos")],
    )
    context.update({"project_owner": PROJECT_OWNER})
    return TEMPLATES.TemplateResponse(request=request, name="quienes_somos.html", context=context)


@router.get("/contacto", response_class=HTMLResponse)
def contacto(request: Request):
    context = seo_context(
        request,
        active_page="contacto",
        page_title="funkcionario.com | Contacto",
        page_description="Contacta con Funkcionario.com para consultas, avisos o incidencias relacionadas con la web.",
        path="/contacto",
        breadcrumbs=[("Inicio", "/"), ("Contacto", "/contacto")],
    )
    context.update(
        {
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
            "project_linkedin": PROJECT_LINKEDIN,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="contacto.html", context=context)


@router.get("/centros/{center_code}", response_class=HTMLResponse)
def center_detail(request: Request, center_code: str):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="center_detail.html",
        context={
            **seo_context(
                request,
                active_page="valencia-docentes",
                page_title=f"funkcionario.com | Centro {center_code}",
                page_description="Ficha técnica de centro docente consultada desde Funkcionario.com.",
                path=f"/centros/{center_code}",
                robots_meta="noindex,follow",
            ),
            "center_code": center_code,
        },
    )


@router.get("/adjudicaciones/{award_result_id}", response_class=HTMLResponse)
def award_detail(request: Request, award_result_id: int):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title=f"funkcionario.com | Adjudicación {award_result_id}",
        page_description="Detalle de adjudicación docente consultado desde Funkcionario.com.",
        path=f"/adjudicaciones/{award_result_id}",
        robots_meta="noindex,follow",
    )
    context.update(
        {
            "award_result_id": award_result_id,
            "official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="award_detail.html", context=context)


@router.get("/dificil-cobertura", response_class=HTMLResponse)
def difficult_coverage(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Difícil cobertura docente",
        page_description=(
            "Consulta puestos docentes de difícil cobertura en la Comunitat Valenciana, "
            "con filtros por especialidad, centro, localidad, fecha y distancia."
        ),
        path="/dificil-cobertura",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes"), ("Difícil cobertura", "/dificil-cobertura")],
    )
    context.update({"official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL})
    return TEMPLATES.TemplateResponse(request=request, name="difficult_coverage.html", context=context)


@router.get("/resultado-dificil-cobertura", response_class=HTMLResponse)
def difficult_coverage_candidates_result(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="funkcionario.com | Candidatos de difícil cobertura",
        page_description="Resultado de candidatos para un puesto de difícil cobertura consultado desde Funkcionario.com.",
        path="/resultado-dificil-cobertura",
        robots_meta="noindex,nofollow",
        breadcrumbs=[("Inicio", "/"), ("Difícil cobertura", "/dificil-cobertura"), ("Resultado", "/resultado-dificil-cobertura")],
    )
    context.update({"official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL})
    return TEMPLATES.TemplateResponse(request=request, name="difficult_coverage_candidates.html", context=context)


@router.get("/404", response_class=HTMLResponse)
def custom_404_preview(request: Request):
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


@router.get("/sw.js", include_in_schema=False)
def service_worker():
    return FileResponse(BASE_DIR / "static" / "js" / "sw.js", media_type="application/javascript")


@router.get("/robots.txt", response_class=PlainTextResponse, include_in_schema=False)
def robots_txt(request: Request) -> PlainTextResponse:
    base_url = get_public_base_url(request)
    content = "\n".join(
        [
            "User-agent: *",
            "Allow: /",
            "Disallow: /api/",
            "Disallow: /resultado-persona",
            "Disallow: /resultado-dificil-cobertura",
            "Disallow: /no-docente/resultado-persona",
            "Disallow: /centros/",
            "Disallow: /adjudicaciones/",
            "Disallow: /404",
            f"Sitemap: {base_url}/sitemap.xml",
            "",
        ]
    )
    return PlainTextResponse(content)


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request) -> Response:
    base_url = get_public_base_url(request)
    urls = []
    for path, priority, changefreq in SITEMAP_PAGES:
        urls.append(
            "  <url>\n"
            f"    <loc>{escape(base_url + path)}</loc>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            "  </url>"
        )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return Response(content=xml, media_type="application/xml")


@router.get("/llms.txt", response_class=PlainTextResponse, include_in_schema=False)
def llms_txt(request: Request) -> PlainTextResponse:
    base_url = get_public_base_url(request)
    content = f"""# Funkcionario.com

Funkcionario.com es una aplicación web de consulta sobre plazas ofertadas, adjudicaciones docentes y puestos de difícil cobertura para personal interino docente en la Comunitat Valenciana.

URL principal: {base_url}

## Qué ofrece

- Consulta de plazas ofertadas docentes.
- Consulta por persona mediante coincidencias de nombre.
- Consulta de puestos de difícil cobertura.
- Consulta de candidatos de difícil cobertura.
- Consulta de plazas, adjudicaciones y bolsas de personal no docente de atención educativa.
- Enlaces a fuentes oficiales de la Conselleria cuando corresponde.
- Cálculo opcional de distancia a centros si el usuario permite ubicación.

## Fuentes

Funkcionario.com trabaja a partir de publicaciones oficiales de RRHH Educación de la Generalitat Valenciana y documentos publicados por Conselleria.

## Páginas principales

- {base_url}/
- {base_url}/valencia-docentes
- {base_url}/valencia-no-docentes
- {base_url}/no-docente/plazas
- {base_url}/no-docente/adjudicaciones
- {base_url}/no-docente/consulta-persona
- {base_url}/plazas-ofertadas
- {base_url}/consulta-persona
- {base_url}/dificil-cobertura
- {base_url}/quienes-somos
- {base_url}/contacto

## Limitaciones

Funkcionario.com no sustituye a la publicación oficial. Los datos deben verificarse siempre con la fuente oficial de Conselleria para trámites, plazos o decisiones administrativas.
"""
    return PlainTextResponse(content)


@router.get("/politica-privacidad", response_class=HTMLResponse)
def politica_privacidad(request: Request):
    context = seo_context(
        request,
        active_page="legal",
        page_title="funkcionario.com | Política de privacidad",
        page_description="Política de privacidad de Funkcionario.com.",
        path="/politica-privacidad",
        breadcrumbs=[("Inicio", "/"), ("Privacidad", "/politica-privacidad")],
    )
    context.update(
        {
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="politica_privacidad.html", context=context)


@router.get("/politica-cookies", response_class=HTMLResponse)
def politica_cookies(request: Request):
    context = seo_context(
        request,
        active_page="legal",
        page_title="funkcionario.com | Política de cookies",
        page_description="Política de cookies y almacenamiento técnico de Funkcionario.com.",
        path="/politica-cookies",
        breadcrumbs=[("Inicio", "/"), ("Cookies", "/politica-cookies")],
    )
    context.update(
        {
            "project_email": PROJECT_EMAIL,
            "project_owner": PROJECT_OWNER,
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="politica_cookies.html", context=context)

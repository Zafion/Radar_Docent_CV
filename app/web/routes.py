from __future__ import annotations

import os
from pathlib import Path
from xml.sax.saxutils import escape

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app.services.telegram_notifications import get_telegram_channel_url
from app.storage.db import get_connection
from app.storage.public_alert_store import count_public_alerts, list_public_alerts
from app.web.i18n import (
    LANGUAGES,
    add_language_context,
    get_language_from_path,
    localized_path,
    localize_json_ld,
    strip_language_prefix,
    translate_text,
)

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
    ("/avisos", "0.8", "hourly"),
    ("/no-docente/plazas", "0.8", "daily"),
    ("/no-docente/adjudicaciones", "0.8", "daily"),
    ("/no-docente/publicaciones", "0.7", "daily"),
    ("/no-docente/consulta-persona", "0.6", "daily"),
    ("/centros", "0.8", "weekly"),
    ("/plazas-ofertadas", "0.8", "daily"),
    ("/consulta-persona", "0.6", "daily"),
    ("/dificil-cobertura", "0.8", "daily"),
    ("/recursos-docentes", "0.7", "weekly"),
    ("/recursos-docentes/primer-dia-docente-interino", "0.7", "monthly"),
    ("/recursos-docentes/mochilas-organizadores-docentes", "0.7", "monthly"),
    ("/recursos-docentes/kit-docente-itinerante", "0.7", "monthly"),
    ("/recursos-docentes/organizacion-oposiciones-bolsas", "0.7", "monthly"),
    ("/recursos-docentes/tecnologia-preparar-clases", "0.7", "monthly"),
    ("/recursos-docentes/desplazamientos-docentes", "0.7", "monthly"),
    ("/quienes-somos", "0.5", "monthly"),
    ("/contacto", "0.4", "monthly"),
    ("/politica-privacidad", "0.3", "yearly"),
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
    lang = get_language_from_path(request.url.path)
    html_lang = LANGUAGES[lang].html_lang
    description = translate_text(DEFAULT_DESCRIPTION, lang)

    return [
        {
            "@context": "https://schema.org",
            "@type": "WebSite",
            "name": "Funkcionario.com",
            "url": base_url,
            "description": description,
            "inLanguage": html_lang,
            "potentialAction": [
                {
                    "@type": "SearchAction",
                    "target": f"{base_url}{localized_path('/consulta-persona', lang)}?q={{{'search_term_string'}}}",
                    "query-input": "required name=search_term_string",
                },
                {
                    "@type": "SearchAction",
                    "target": f"{base_url}{localized_path('/centros', lang)}?q={{{'search_term_string'}}}",
                    "query-input": "required name=search_term_string",
                },
            ],
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
    lang = get_language_from_path(request.url.path)
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": [
            {
                "@type": "ListItem",
                "position": index,
                "name": translate_text(label, lang),
                "item": absolute_url(request, localized_path(path, lang)),
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
    lang = get_language_from_path(request.url.path)
    html_lang = LANGUAGES[lang].html_lang
    clean_path = strip_language_prefix(path)
    page_path = localized_path(clean_path, lang)
    translated_title = translate_text(page_title, lang)
    translated_description = translate_text(page_description, lang)
    json_ld = build_base_json_ld(request)

    if breadcrumbs:
        json_ld.append(build_breadcrumb_json_ld(request, breadcrumbs))

    json_ld.append(
        {
            "@context": "https://schema.org",
            "@type": "WebPage",
            "name": translated_title,
            "description": translated_description,
            "url": absolute_url(request, page_path),
            "inLanguage": html_lang,
            "isPartOf": {
                "@type": "WebSite",
                "name": "Funkcionario.com",
                "url": get_public_base_url(request),
            },
        }
    )

    if extra_json_ld:
        json_ld.extend(localize_json_ld(extra_json_ld, lang))

    context = {
        "active_page": active_page,
        "page_title": translated_title,
        "page_description": translated_description,
        "canonical_url": absolute_url(request, page_path),
        "robots_meta": robots_meta,
        "og_type": page_type,
        "og_image_url": absolute_url(request, "/static/img/og-image.png"),
        "site_name": "Funkcionario.com",
        "seo_json_ld": json_ld,
    }
    return add_language_context(request, context)


@router.get("/es", include_in_schema=False)
@router.get("/es/{full_path:path}", include_in_schema=False)
def spanish_language_alias(request: Request, full_path: str = ""):
    target = "/" + full_path.strip("/") if full_path else "/"
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=301)

@router.get("/va", response_class=HTMLResponse)
@router.get("/va/", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    context = seo_context(
        request,
        active_page="home",
        page_title="Funkcionario.com | Plazas y adjudicaciones docentes Comunidad Valenciana",
        page_description=(
            "Consulta en Funkcionario.com plazas ofertadas, adjudicaciones docentes, "
            "difícil cobertura y resultados de personal interino docente en la Comunitat Valenciana."
        ),
        path="/",
        breadcrumbs=[("Inicio", "/")],
    )
    context.update({"telegram_channel_url": get_telegram_channel_url()})
    return TEMPLATES.TemplateResponse(request=request, name="home.html", context=context)


@router.get("/va/valencia-docentes", response_class=HTMLResponse)
@router.get("/valencia-docentes", response_class=HTMLResponse)
def valencia_docentes(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="Plazas y adjudicaciones docentes Comunidad Valenciana | Funkcionario.com",
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


@router.get("/va/recursos-docentes", response_class=HTMLResponse)
@router.get("/recursos-docentes", response_class=HTMLResponse)
def teacher_resources(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Recursos per a docents | Guies pràctiques | Funkcionario.com"
        page_description = (
            "Guies pràctiques de Funkcionario.com sobre organització, material, tecnologia "
            "i desplaçaments per a personal docent interí."
        )
        breadcrumbs = [("Inici", "/"), ("Recursos per a docents", "/recursos-docentes")]
    else:
        page_title = "Recursos para docentes | Guías prácticas | Funkcionario.com"
        page_description = (
            "Guías prácticas de Funkcionario.com sobre organización, material, tecnología "
            "y desplazamientos para personal docente interino."
        )
        breadcrumbs = [("Inicio", "/"), ("Recursos para docentes", "/recursos-docentes")]

    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path="/recursos-docentes",
        breadcrumbs=breadcrumbs,
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resources.html",
        context=context,
    )


@router.get("/va/recursos-docentes/primer-dia-docente-interino", response_class=HTMLResponse)
@router.get("/recursos-docentes/primer-dia-docente-interino", response_class=HTMLResponse)
def teacher_resource_first_day(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Què portar el primer dia com a docent interí | Funkcionario.com"
        page_description = (
            "Guia pràctica sobre documentació, material, tecnologia i organització "
            "per al primer dia d'incorporació a un centre docent."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Primer dia com a docent interí", "/recursos-docentes/primer-dia-docente-interino"),
        ]
        headline = "Què portar el primer dia com a docent interí"
    else:
        page_title = "Qué llevar el primer día como docente interino | Funkcionario.com"
        page_description = (
            "Guía práctica sobre documentación, material, tecnología y organización "
            "para el primer día de incorporación a un centro docente."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Primer día como docente interino", "/recursos-docentes/primer-dia-docente-interino"),
        ]
        headline = "Qué llevar el primer día como docente interino"

    article_path = "/recursos-docentes/primer-dia-docente-interino"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-19",
                "dateModified": "2026-07-19",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_first_day.html",
        context=context,
    )


@router.get("/va/recursos-docentes/mochilas-organizadores-docentes", response_class=HTMLResponse)
@router.get("/recursos-docentes/mochilas-organizadores-docentes", response_class=HTMLResponse)
def teacher_resource_bags_organizers(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Motxilles, bosses i organitzadors per a docents | Funkcionario.com"
        page_description = (
            "Guia pràctica per a triar motxilla, bossa o organitzadors segons el material, "
            "el portàtil i el tipus de desplaçament docent."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Motxilles i organitzadors", "/recursos-docentes/mochilas-organizadores-docentes"),
        ]
        headline = "Motxilles, bosses i organitzadors per a docents"
    else:
        page_title = "Mochilas, bolsas y organizadores para docentes | Funkcionario.com"
        page_description = (
            "Guía práctica para elegir mochila, bolsa u organizadores según el material, "
            "el portátil y el tipo de desplazamiento docente."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Mochilas y organizadores", "/recursos-docentes/mochilas-organizadores-docentes"),
        ]
        headline = "Mochilas, bolsas y organizadores para docentes"

    article_path = "/recursos-docentes/mochilas-organizadores-docentes"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-20",
                "dateModified": "2026-07-20",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_bags_organizers.html",
        context=context,
    )


@router.get("/va/recursos-docentes/kit-docente-itinerante", response_class=HTMLResponse)
@router.get("/recursos-docentes/kit-docente-itinerante", response_class=HTMLResponse)
def teacher_resource_itinerant_kit(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Kit pràctic per a docents itinerants | Funkcionario.com"
        page_description = (
            "Guia pràctica per a preparar un kit lleuger i modular per a docents "
            "que canvien d'aula, d'edifici o de centre durant la jornada."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Kit per a docents itinerants", "/recursos-docentes/kit-docente-itinerante"),
        ]
        headline = "Kit pràctic per a docents itinerants"
    else:
        page_title = "Kit práctico para docentes itinerantes | Funkcionario.com"
        page_description = (
            "Guía práctica para preparar un kit ligero y modular para docentes "
            "que cambian de aula, edificio o centro durante la jornada."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Kit para docentes itinerantes", "/recursos-docentes/kit-docente-itinerante"),
        ]
        headline = "Kit práctico para docentes itinerantes"

    article_path = "/recursos-docentes/kit-docente-itinerante"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-21",
                "dateModified": "2026-07-21",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_itinerant_kit.html",
        context=context,
    )


@router.get("/va/recursos-docentes/organizacion-oposiciones-bolsas", response_class=HTMLResponse)
@router.get("/recursos-docentes/organizacion-oposiciones-bolsas", response_class=HTMLResponse)
def teacher_resource_document_organization(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Com organitzar oposicions, borses i documentació docent | Funkcionario.com"
        page_description = (
            "Guia pràctica per a ordenar convocatòries, terminis, mèrits, certificats "
            "i justificants relacionats amb oposicions i borses docents."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Organització d'oposicions i borses", "/recursos-docentes/organizacion-oposiciones-bolsas"),
        ]
        headline = "Com organitzar oposicions, borses i documentació docent"
    else:
        page_title = "Cómo organizar oposiciones, bolsas y documentación docente | Funkcionario.com"
        page_description = (
            "Guía práctica para ordenar convocatorias, plazos, méritos, certificados "
            "y justificantes relacionados con oposiciones y bolsas docentes."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Organización de oposiciones y bolsas", "/recursos-docentes/organizacion-oposiciones-bolsas"),
        ]
        headline = "Cómo organizar oposiciones, bolsas y documentación docente"

    article_path = "/recursos-docentes/organizacion-oposiciones-bolsas"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-22",
                "dateModified": "2026-07-22",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_document_organization.html",
        context=context,
    )


@router.get("/va/recursos-docentes/tecnologia-preparar-clases", response_class=HTMLResponse)
@router.get("/recursos-docentes/tecnologia-preparar-clases", response_class=HTMLResponse)
def teacher_resource_teaching_technology(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Tecnologia útil per a preparar classes i corregir treballs | Funkcionario.com"
        page_description = (
            "Guia pràctica sobre accessoris i eines tecnològiques útils per a preparar classes, "
            "corregir treballs, connectar dispositius a l'aula i protegir els materials docents."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Tecnologia per a preparar classes", "/recursos-docentes/tecnologia-preparar-clases"),
        ]
        headline = "Tecnologia útil per a preparar classes i corregir treballs"
    else:
        page_title = "Tecnología útil para preparar clases y corregir trabajos | Funkcionario.com"
        page_description = (
            "Guía práctica sobre accesorios y herramientas tecnológicas útiles para preparar clases, "
            "corregir trabajos, conectar dispositivos en el aula y proteger los materiales docentes."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Tecnología para preparar clases", "/recursos-docentes/tecnologia-preparar-clases"),
        ]
        headline = "Tecnología útil para preparar clases y corregir trabajos"

    article_path = "/recursos-docentes/tecnologia-preparar-clases"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-23",
                "dateModified": "2026-07-23",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_teaching_technology.html",
        context=context,
    )


@router.get("/va/recursos-docentes/desplazamientos-docentes", response_class=HTMLResponse)
@router.get("/recursos-docentes/desplazamientos-docentes", response_class=HTMLResponse)
def teacher_resource_commuting(request: Request):
    lang = get_language_from_path(request.url.path)

    if lang == "va":
        page_title = "Recursos per a docents desplaçats a una altra localitat | Funkcionario.com"
        page_description = (
            "Guia pràctica per a organitzar desplaçaments docents, transport, menjars, "
            "càrrega de dispositius, despeses i estades temporals en una altra localitat."
        )
        breadcrumbs = [
            ("Inici", "/"),
            ("Recursos per a docents", "/recursos-docentes"),
            ("Desplaçaments docents", "/recursos-docentes/desplazamientos-docentes"),
        ]
        headline = "Recursos per a docents desplaçats a una altra localitat"
    else:
        page_title = "Recursos para docentes desplazados a otra localidad | Funkcionario.com"
        page_description = (
            "Guía práctica para organizar desplazamientos docentes, transporte, comidas, "
            "carga de dispositivos, gastos y estancias temporales en otra localidad."
        )
        breadcrumbs = [
            ("Inicio", "/"),
            ("Recursos para docentes", "/recursos-docentes"),
            ("Desplazamientos docentes", "/recursos-docentes/desplazamientos-docentes"),
        ]
        headline = "Recursos para docentes desplazados a otra localidad"

    article_path = "/recursos-docentes/desplazamientos-docentes"
    context = seo_context(
        request,
        active_page="recursos-docentes",
        page_title=page_title,
        page_description=page_description,
        path=article_path,
        breadcrumbs=breadcrumbs,
        page_type="article",
        extra_json_ld=[
            {
                "@context": "https://schema.org",
                "@type": "Article",
                "headline": headline,
                "description": page_description,
                "mainEntityOfPage": absolute_url(request, localized_path(article_path, lang)),
                "author": {
                    "@type": "Person",
                    "name": PROJECT_OWNER,
                },
                "publisher": {
                    "@type": "Organization",
                    "name": "Funkcionario.com",
                    "url": get_public_base_url(request),
                },
                "datePublished": "2026-07-24",
                "dateModified": "2026-07-24",
            }
        ],
    )
    return TEMPLATES.TemplateResponse(
        request=request,
        name="teacher_resource_commuting.html",
        context=context,
    )


@router.get("/va/valencia-no-docentes", response_class=HTMLResponse)
@router.get("/valencia-no-docentes", response_class=HTMLResponse)
def valencia_no_docentes(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="Personal no docente Comunidad Valenciana | Plazas, adjudicaciones y bolsas",
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


@router.get("/va/avisos", response_class=HTMLResponse)
@router.get("/avisos", response_class=HTMLResponse)
def public_alerts_page(request: Request):
    context = seo_context(
        request,
        active_page="avisos",
        page_title="Avisos de publicaciones oficiales | Funkcionario.com",
        page_description=(
            "Últimos avisos públicos de Funkcionario.com sobre plazas, adjudicaciones, "
            "difícil cobertura y publicaciones de personal docente y no docente en la Comunitat Valenciana."
        ),
        path="/avisos",
        breadcrumbs=[("Inicio", "/"), ("Avisos", "/avisos")],
    )
    conn = get_connection()
    alerts = list_public_alerts(conn, limit=50, offset=0)
    total = count_public_alerts(conn)
    context.update(
        {
            "alerts": alerts,
            "alerts_total": total,
            "telegram_channel_url": get_telegram_channel_url(),
        }
    )
    return TEMPLATES.TemplateResponse(request=request, name="public_alerts.html", context=context)


def _feed_alerts(limit: int = 50) -> list[dict]:
    conn = get_connection()
    return list_public_alerts(conn, limit=limit, offset=0)


@router.get("/feed.xml", include_in_schema=False)
def alerts_feed_xml(request: Request) -> Response:
    base_url = get_public_base_url(request)
    alerts = _feed_alerts(limit=50)
    items = []
    for alert in alerts:
        link = alert.get("public_url") or "/avisos"
        if not str(link).startswith(("http://", "https://")):
            link = f"{base_url}{link}"
        pub_date = alert.get("detected_at") or alert.get("created_at") or ""
        items.append(
            "  <item>\n"
            f"    <title>{escape(str(alert.get('title') or 'Aviso Funkcionario'))}</title>\n"
            f"    <link>{escape(str(link))}</link>\n"
            f"    <guid isPermaLink=\"false\">{escape(str(alert.get('event_key') or alert.get('id')))}</guid>\n"
            f"    <description>{escape(str(alert.get('summary') or ''))}</description>\n"
            f"    <pubDate>{escape(str(pub_date))}</pubDate>\n"
            "  </item>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '<channel>\n'
        '  <title>Funkcionario.com · Avisos</title>\n'
        f"  <link>{escape(base_url + '/avisos')}</link>\n"
        '  <description>Avisos públicos de nuevas publicaciones oficiales procesadas por Funkcionario.com.</description>\n'
        + "\n".join(items)
        + "\n</channel>\n</rss>\n"
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@router.get("/feed.json", include_in_schema=False)
def alerts_feed_json(request: Request) -> JSONResponse:
    base_url = get_public_base_url(request)
    alerts = _feed_alerts(limit=50)
    items = []
    for alert in alerts:
        link = alert.get("public_url") or "/avisos"
        if not str(link).startswith(("http://", "https://")):
            link = f"{base_url}{link}"
        items.append(
            {
                "id": str(alert.get("event_key") or alert.get("id")),
                "url": link,
                "title": alert.get("title"),
                "content_text": alert.get("summary"),
                "date_published": alert.get("detected_at") or alert.get("created_at"),
            }
        )
    return JSONResponse(
        {
            "version": "https://jsonfeed.org/version/1.1",
            "title": "Funkcionario.com · Avisos",
            "home_page_url": base_url,
            "feed_url": f"{base_url}/feed.json",
            "items": items,
        }
    )


def _redirect_with_query(request: Request, target: str) -> RedirectResponse:
    if request.url.query:
        target = f"{target}?{request.url.query}"
    return RedirectResponse(url=target, status_code=301)


# Compatibilidad con slugs valencianizados generados por la primera versión i18n.
# La URL canónica mantiene el slug técnico original para no duplicar páginas ni romper APIs.
@router.get("/va/no-docente/adjudicacions", include_in_schema=False)
@router.get("/no-docente/adjudicacions", include_in_schema=False)
def non_docent_awards_legacy_slug(request: Request):
    target = "/va/no-docente/adjudicaciones" if get_language_from_path(request.url.path) == "va" else "/no-docente/adjudicaciones"
    return _redirect_with_query(request, target)


@router.get("/va/adjudicacions/{award_result_id}", include_in_schema=False)
@router.get("/adjudicacions/{award_result_id}", include_in_schema=False)
def award_detail_legacy_slug(request: Request, award_result_id: int):
    target = f"/va/adjudicaciones/{award_result_id}" if get_language_from_path(request.url.path) == "va" else f"/adjudicaciones/{award_result_id}"
    return _redirect_with_query(request, target)


@router.get("/va/no-docente/plazas", response_class=HTMLResponse)
@router.get("/no-docente/plazas", response_class=HTMLResponse)
def non_docent_positions(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="Plazas personal no docente Educación Valencia | Convocatorias ADC EDU",
        page_description=(
            "Consulta plazas ADC ofertadas para personal no docente de atención educativa "
            "en la Comunitat Valenciana."
        ),
        path="/no-docente/plazas",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Plazas", "/no-docente/plazas")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_positions.html", context=context)


@router.get("/va/no-docente/adjudicaciones", response_class=HTMLResponse)
@router.get("/no-docente/adjudicaciones", response_class=HTMLResponse)
def non_docent_awards(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="Adjudicaciones personal no docente Valencia | ADC EDU",
        page_description=(
            "Consulta adjudicaciones ADC publicadas para personal no docente de atención educativa."
        ),
        path="/no-docente/adjudicaciones",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Adjudicaciones", "/no-docente/adjudicaciones")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_awards.html", context=context)


@router.get("/va/no-docente/publicaciones", response_class=HTMLResponse)
@router.get("/no-docente/publicaciones", response_class=HTMLResponse)
def non_docent_publications(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="Publicaciones personal no docente Valencia | Funkcionario.com",
        page_description=(
            "Consulta publicaciones oficiales de personal no docente de atención educativa "
            "detectadas y procesadas desde fuentes de Conselleria."
        ),
        path="/no-docente/publicaciones",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Publicaciones", "/no-docente/publicaciones")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_publications.html", context=context)


@router.get("/va/no-docente/consulta-persona", response_class=HTMLResponse)
@router.get("/no-docente/consulta-persona", response_class=HTMLResponse)
def non_docent_person_search(request: Request):
    context = seo_context(
        request,
        active_page="valencia-no-docentes",
        page_title="Consulta personal no docente por persona | Funkcionario.com",
        page_description=(
            "Busca una persona en adjudicaciones y bolsas no docentes de atención educativa."
        ),
        path="/no-docente/consulta-persona",
        breadcrumbs=[("Inicio", "/"), ("Consulta no docentes", "/valencia-no-docentes"), ("Consulta por persona", "/no-docente/consulta-persona")],
    )
    context.update({"official_non_docent_base_url": OFFICIAL_NON_DOCENT_BASE_URL})
    return TEMPLATES.TemplateResponse(request=request, name="non_docent_person_search.html", context=context)


@router.get("/va/no-docente/resultado-persona", response_class=HTMLResponse)
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


@router.get("/va/plazas-ofertadas", response_class=HTMLResponse)
@router.get("/plazas-ofertadas", response_class=HTMLResponse)
def offered_positions(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="Plazas docentes ofertadas Comunidad Valenciana | Funkcionario.com",
        page_description=(
            "Consulta las últimas plazas docentes ofertadas en la Comunitat Valenciana, "
            "con filtros por fecha, localidad, centro, especialidad y distancia aproximada."
        ),
        path="/plazas-ofertadas",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes"), ("Plazas ofertadas", "/plazas-ofertadas")],
    )
    context.update({"official_adjudicaciones_url": OFFICIAL_ADJUDICACIONES_URL})
    return TEMPLATES.TemplateResponse(request=request, name="offered_positions.html", context=context)


@router.get("/va/consulta-persona", response_class=HTMLResponse)
@router.get("/consulta-persona", response_class=HTMLResponse)
def person_search(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="Consulta adjudicaciones docentes por persona | Funkcionario.com",
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


@router.get("/va/resultado-persona", response_class=HTMLResponse)
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


@router.get("/va/quienes-somos", response_class=HTMLResponse)
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


@router.get("/va/contacto", response_class=HTMLResponse)
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


@router.get("/va/centros", response_class=HTMLResponse)
@router.get("/centros", response_class=HTMLResponse)
def center_search(request: Request):
    return TEMPLATES.TemplateResponse(
        request=request,
        name="center_search.html",
        context={
            **seo_context(
                request,
                active_page="centros",
                page_title="Buscador de centros educativos Comunidad Valenciana | Funkcionario.com",
                page_description="Busca centros educativos de la Comunitat Valenciana por nombre, código, localidad o provincia y consulta mapa, ruta y distancia aproximada.",
                path="/centros",
                breadcrumbs=[("Inicio", "/"), ("Centros", "/centros")],
            ),
        },
    )


@router.get("/va/centros/{center_code}", response_class=HTMLResponse)
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


@router.get("/va/adjudicaciones/{award_result_id}", response_class=HTMLResponse)
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


@router.get("/va/dificil-cobertura", response_class=HTMLResponse)
@router.get("/dificil-cobertura", response_class=HTMLResponse)
def difficult_coverage(request: Request):
    context = seo_context(
        request,
        active_page="valencia-docentes",
        page_title="Difícil cobertura docentes Comunidad Valenciana | Funkcionario.com",
        page_description=(
            "Consulta puestos docentes de difícil cobertura en la Comunitat Valenciana, "
            "con filtros por especialidad, centro, localidad, fecha y distancia."
        ),
        path="/dificil-cobertura",
        breadcrumbs=[("Inicio", "/"), ("Consulta docentes", "/valencia-docentes"), ("Difícil cobertura", "/dificil-cobertura")],
    )
    context.update({"official_adjudicaciones_continuas_url": OFFICIAL_ADJUDICACIONES_CONTINUAS_URL})
    return TEMPLATES.TemplateResponse(request=request, name="difficult_coverage.html", context=context)


@router.get("/va/resultado-dificil-cobertura", response_class=HTMLResponse)
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


@router.get("/va/404", response_class=HTMLResponse)
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
            "Disallow: /404",
            "Disallow: /va/resultado-persona",
            "Disallow: /va/resultado-dificil-cobertura",
            "Disallow: /va/no-docente/resultado-persona",
            "Disallow: /va/404",
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
        for lang in ("es", "va"):
            loc_path = localized_path(path, lang)
            alternates = "\n".join(
                f'    <xhtml:link rel="alternate" hreflang="{LANGUAGES[alt_lang].hreflang}" href="{escape(base_url + localized_path(path, alt_lang))}" />'
                for alt_lang in ("es", "va")
            )
            x_default = f'    <xhtml:link rel="alternate" hreflang="x-default" href="{escape(base_url + localized_path(path, "es"))}" />'
            urls.append(
                "  <url>\n"
                f"    <loc>{escape(base_url + loc_path)}</loc>\n"
                f"{alternates}\n"
                f"{x_default}\n"
                f"    <changefreq>{changefreq}</changefreq>\n"
                f"    <priority>{priority}</priority>\n"
                "  </url>"
            )

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n'
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
- {base_url}/avisos
- {base_url}/feed.xml
- {base_url}/feed.json
- {base_url}/no-docente/plazas
- {base_url}/no-docente/adjudicaciones
- {base_url}/no-docente/consulta-persona
- {base_url}/plazas-ofertadas
- {base_url}/consulta-persona
- {base_url}/dificil-cobertura
- {base_url}/quienes-somos
- {base_url}/contacto
- {base_url}/va
- {base_url}/va/valencia-docentes
- {base_url}/va/valencia-no-docentes
- {base_url}/va/avisos
- {base_url}/va/plazas-ofertadas
- {base_url}/va/consulta-persona
- {base_url}/va/dificil-cobertura

## Limitaciones

Funkcionario.com no sustituye a la publicación oficial. Los datos deben verificarse siempre con la fuente oficial de Conselleria para trámites, plazos o decisiones administrativas.
"""
    return PlainTextResponse(content)


@router.get("/va/politica-privacidad", response_class=HTMLResponse)
@router.get("/politica-privacidad", response_class=HTMLResponse)
def politica_privacidad(request: Request):
    context = seo_context(
        request,
        active_page="legal",
        page_title="Política de Privacidad y Cookies | Funkcionario.com",
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


@router.get("/va/politica-cookies", include_in_schema=False)
@router.get("/politica-cookies", include_in_schema=False)
def politica_cookies_redirect(request: Request):
    target = "/va/politica-privacidad" if get_language_from_path(request.url.path) == "va" else "/politica-privacidad"
    return RedirectResponse(url=target, status_code=301)

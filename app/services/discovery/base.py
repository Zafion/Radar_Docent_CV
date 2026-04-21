from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, replace
import re
import unicodedata
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag


DATE_RE = re.compile(r"\b(\d{2}/\d{2}/\d{4})\b")


@dataclass(slots=True)
class DiscoveredAsset:
    source_key: str
    source_url: str
    publication_label: Optional[str]
    publication_date_text: Optional[str]
    asset_role: str
    title: str
    url: str
    canonical_url: str
    section: Optional[str]
    downloadable: bool


class BaseDiscoveryAdapter(ABC):
    source_key: str = ""
    source_url: str = ""
    source_label: str = ""

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.headers = {
            "User-Agent": "RadarDocentCV/0.7 (+https://ceice.gva.es/)"
        }

    @abstractmethod
    def discover_assets(self) -> list[DiscoveredAsset]:
        raise NotImplementedError

    def _fetch_html(self) -> str:
        response = httpx.get(
            self.source_url,
            headers=self.headers,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return response.text

    def _get_soup(self) -> BeautifulSoup:
        return BeautifulSoup(self._fetch_html(), "html.parser")

    def _fetch_response(self, url: str) -> httpx.Response | None:
        try:
            response = httpx.get(
                url,
                headers=self.headers,
                timeout=self.timeout,
                follow_redirects=True,
            )
            if response.status_code >= 400:
                return None
            return response
        except httpx.HTTPError:
            return None

    def _absolute_url(self, href: str, base_url: str | None = None) -> str:
        return urljoin(base_url or self.source_url, href)

    def _canonicalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                "",
                "",
            )
        )

    def _clean_text(self, value: str) -> str:
        return " ".join(value.split())

    def _normalize_match_text(self, value: str) -> str:
        value = value.strip().lower()
        value = unicodedata.normalize("NFKD", value)
        value = "".join(ch for ch in value if not unicodedata.combining(ch))
        value = re.sub(r"\s+", " ", value)
        return value

    def _find_previous_heading(self, anchor: Tag) -> Optional[str]:
        previous = anchor.find_previous(["h1", "h2", "h3", "h4", "strong", "b"])
        if not previous:
            return None

        text = self._clean_text(previous.get_text(" ", strip=True))
        return text or None

    def _looks_like_downloadable_document(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return (
            path.endswith(".pdf")
            or ".pdf/" in path
            or "/documents/" in path
            or "/auto/" in path
            or "/file-system/" in path
        )

    def _looks_like_html_response(self, response: httpx.Response) -> bool:
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" in content_type or "application/xhtml+xml" in content_type:
            return True

        path = urlparse(str(response.url)).path.lower()
        return not self._looks_like_downloadable_document(path)

    def _is_ignorable_href(self, href: str) -> bool:
        href = href.strip().lower()
        return (
            not href
            or href.startswith("#")
            or href.startswith("javascript:")
            or href.startswith("mailto:")
            or href.startswith("tel:")
        )

    def _is_rrhh_educacion_html_url(self, url: str) -> bool:
        parsed = urlparse(url)
        if parsed.netloc.lower() != "ceice.gva.es":
            return False

        path = parsed.path.lower()
        return path.startswith("/es/web/rrhh-educacion")

    def _should_follow_html_url(self, url: str) -> bool:
        if not self._is_rrhh_educacion_html_url(url):
            return False

        if self._looks_like_downloadable_document(url):
            return False

        path = urlparse(url).path.lower()
        blocked_suffixes = (
            ".pdf",
            ".doc",
            ".docx",
            ".xls",
            ".xlsx",
            ".zip",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
        )
        return not path.endswith(blocked_suffixes)

    def _extract_page_title(self, soup: BeautifulSoup) -> Optional[str]:
        h1 = soup.find("h1")
        if h1:
            text = self._clean_text(h1.get_text(" ", strip=True))
            if text:
                return text

        title_tag = soup.find("title")
        if title_tag:
            text = self._clean_text(title_tag.get_text(" ", strip=True))
            if text:
                return text

        return None

    def _extract_first_date(self, text: str) -> Optional[str]:
        match = DATE_RE.search(text)
        if not match:
            return None
        return match.group(1)

    def _guess_anchor_publication_date(self, anchor: Tag) -> Optional[str]:
        candidates: list[str] = []

        current: Tag | None = anchor
        hops = 0
        while current is not None and hops < 5:
            text = self._clean_text(current.get_text(" ", strip=True))
            if text:
                candidates.append(text)
            current = current.parent if isinstance(current.parent, Tag) else None
            hops += 1

        previous_text_nodes = []
        previous = anchor
        for _ in range(3):
            previous = previous.find_previous()
            if previous is None:
                break
            if isinstance(previous, Tag):
                text = self._clean_text(previous.get_text(" ", strip=True))
                if text:
                    previous_text_nodes.append(text)

        candidates.extend(previous_text_nodes)

        for text in candidates:
            date_text = self._extract_first_date(text)
            if date_text:
                return date_text

        return None

    def _guess_anchor_title(self, anchor: Tag, absolute_url: str) -> str:
        title = self._clean_text(anchor.get_text(" ", strip=True))
        if title:
            return title

        for attr_name in ("title", "aria-label"):
            attr_value = anchor.get(attr_name)
            if attr_value:
                text = self._clean_text(str(attr_value))
                if text:
                    return text

        filename = urlparse(absolute_url).path.rstrip("/").split("/")[-1]
        return filename or "Documento PDF"

    def _guess_anchor_section(
        self,
        anchor: Tag,
        page_title: Optional[str],
    ) -> Optional[str]:
        previous_heading = self._find_previous_heading(anchor)
        if previous_heading:
            return previous_heading

        return page_title

    def _merge_asset(
        self,
        existing: DiscoveredAsset,
        incoming: DiscoveredAsset,
    ) -> DiscoveredAsset:
        title = existing.title
        if (
            existing.title.lower().endswith(".pdf")
            and not incoming.title.lower().endswith(".pdf")
        ):
            title = incoming.title
        elif len(incoming.title) > len(existing.title):
            title = incoming.title

        publication_date_text = (
            existing.publication_date_text or incoming.publication_date_text
        )
        publication_label = existing.publication_label or incoming.publication_label
        section = existing.section or incoming.section

        return replace(
            existing,
            title=title,
            publication_date_text=publication_date_text,
            publication_label=publication_label,
            section=section,
        )

    def _is_relevant_pdf_candidate(
        self,
        *,
        title: str,
        section: Optional[str],
        page_title: Optional[str],
        absolute_url: str,
    ) -> bool:
        combined = " ".join(
            part for part in [title, section or "", page_title or "", absolute_url] if part
        )
        text = self._normalize_match_text(combined)

        strong_positive_markers = (
            "adjudicacio",
            "adjudicacion",
            "listado de adjudicacion",
            "listat d'adjudicacio",
            "puestos ofertados",
            "llocs ofertats",
            "puestos provisionales ofertados",
            "puestos definitivos ofertados",
            "listado de vacantes",
            "vacantes definitivas",
            "dificil cobertura",
            "dificil provisio",
            "dificil provision",
            "puesto asignado provisionalmente",
            "lloc assignat provisionalment",
        )

        strong_negative_markers = (
            "oferta de empleo publico",
            "procedimiento selectivo",
            "concurso oposicion",
            "concurso-oposicion",
            "concurso de meritos",
            "estabilizacion",
            "estabilizacion",
            "organos de seleccion",
            "tribunales",
            "especialidades y titulaciones",
            "especialitats i titulacions",
            "compatibilidad",
            "compatibilidades",
            "muface",
            "excedencia",
            "acoso",
            "delitos sexuales",
            "delincuentes sexuales",
            "manual de ayuda",
            "preguntas frecuentes",
            "faq",
            "declaracion jurada",
            "declaracion responsable",
            "protocolo",
            "certificado acreditativo",
            "documento de requisitos tecnicos",
            "boe-a-",
        )

        if any(marker in text for marker in strong_negative_markers):
            return False

        return any(marker in text for marker in strong_positive_markers)

    def _crawl_seed_urls(
        self,
        *,
        seed_urls: list[str],
        max_depth: int = 2,
        asset_role: str = "pdf_candidate",
    ) -> list[DiscoveredAsset]:
        queue: deque[tuple[str, int]] = deque()
        visited_pages: set[str] = set()
        assets_by_url: dict[str, DiscoveredAsset] = {}

        for seed_url in seed_urls:
            queue.append((seed_url, 0))

        while queue:
            current_url, depth = queue.popleft()
            canonical_page_url = self._canonicalize_url(current_url)

            if canonical_page_url in visited_pages:
                continue
            visited_pages.add(canonical_page_url)

            response = self._fetch_response(current_url)
            if response is None:
                continue

            if not self._looks_like_html_response(response):
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            page_title = self._extract_page_title(soup) or self.source_label or self.source_key

            for anchor in soup.find_all("a", href=True):
                href = (anchor.get("href") or "").strip()
                if self._is_ignorable_href(href):
                    continue

                absolute_url = self._absolute_url(href, base_url=str(response.url))
                canonical_url = self._canonicalize_url(absolute_url)

                if self._looks_like_downloadable_document(absolute_url):
                    title = self._guess_anchor_title(anchor, absolute_url)
                    section = self._guess_anchor_section(anchor, page_title)

                    if not self._is_relevant_pdf_candidate(
                        title=title,
                        section=section,
                        page_title=page_title,
                        absolute_url=absolute_url,
                    ):
                        continue

                    discovered = DiscoveredAsset(
                        source_key=self.source_key,
                        source_url=self.source_url,
                        publication_label=page_title,
                        publication_date_text=self._guess_anchor_publication_date(anchor),
                        asset_role=asset_role,
                        title=title,
                        url=absolute_url,
                        canonical_url=canonical_url,
                        section=section,
                        downloadable=True,
                    )

                    existing = assets_by_url.get(canonical_url)
                    if existing is None:
                        assets_by_url[canonical_url] = discovered
                    else:
                        assets_by_url[canonical_url] = self._merge_asset(existing, discovered)

                    continue

                if depth >= max_depth:
                    continue

                if self._should_follow_html_url(absolute_url):
                    queue.append((absolute_url, depth + 1))

        return list(assets_by_url.values())
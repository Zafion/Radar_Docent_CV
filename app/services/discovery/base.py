from __future__ import annotations

from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re
import unicodedata
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag
from app.services.discovery.http_policy import PolicyClient, ResponseDecodingError


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
        self.discovery_errors: list[dict] = []
        self.captured_pages: list[dict] = []
        self._last_response_url: str | None = None
        self._document_base_url: str | None = None
        self.headers = {
            "User-Agent": "RadarDocentCV/0.7 (+https://funkcionario.com)"
        }
        self._network_client = PolicyClient(user_agent=self.headers['User-Agent'],timeout=timeout,max_bytes=5*1024*1024)

    @abstractmethod
    def discover_assets(self) -> list[DiscoveredAsset]:
        raise NotImplementedError

    def _fetch_html(self) -> str:
        response = self._fetch_response(self.source_url)
        if response is None:
            raise ValueError(f"discovery_fetch_failed: {self.discovery_errors}")
        return response.text

    def _get_soup(self) -> BeautifulSoup:
        soup=BeautifulSoup(self._fetch_html(), "html.parser")
        self._document_base_url=self._page_base(soup,self._last_response_url or self.source_url)
        return soup

    def _page_base(self, soup: BeautifulSoup, final_url: str) -> str:
        bases=soup.find_all('base',href=True)
        if len(bases)>1:
            raise ValueError('ambiguous_html_base')
        return urljoin(final_url,bases[0]['href']) if bases else final_url

    def _fetch_response(self, url: str) -> httpx.Response | None:
        try:
            with self._network_client.stream('GET',url,headers={'Accept-Encoding': 'identity'}) as response:
                pass
            if response.status_code >= 400:
                self.discovery_errors.append({
                    "url": url, "kind": "http_error", "status_code": response.status_code,
                    "retry_after": response.headers.get("retry-after"),
                })
                return None
            if self._looks_like_html_response(response):
                self._last_response_url=str(response.url)
                self.captured_pages.append({'page_url':url,'final_page_url':str(response.url),
                                            'observed_utc':datetime.now(timezone.utc).isoformat(),
                                            'html':response.content})
            return response
        except ResponseDecodingError as exc:
            unexpected = exc.cause_type == 'UnexpectedContentEncoding'
            self.discovery_errors.append({
                'url': exc.url, 'kind': 'unexpected_content_encoding' if unexpected else 'decoding_error',
                'status_code': exc.status_code, 'content_encoding': exc.content_encoding,
                'error_type': 'UnexpectedContentEncoding' if unexpected else 'DecodingError',
                'cause_type': exc.cause_type,
            })
            return None
        except httpx.HTTPError as exc:
            self.discovery_errors.append({
                "url": url, "kind": "timeout" if isinstance(exc, httpx.TimeoutException) else "connection_error",
                "status_code": None, "error_type": type(exc).__name__,
            })
            return None

    def _absolute_url(self, href: str, base_url: str | None = None) -> str:
        return urljoin(base_url or self._document_base_url or self._last_response_url or self.source_url, href)

    def _canonicalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        return urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                "",
                # HTML queries select pages/filters; dropping them hides links.
                # Retain established document keys and download deduplication.
                "" if self._looks_like_downloadable_document(url) else parsed.query,
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
        return re.match(r"^/(?:es|va)/web/rrhh-educacion(?:/|$)", path) is not None

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

        publication_date_text = self._newest_date_text(
            existing.publication_date_text,
            incoming.publication_date_text,
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


    def _newest_date_text(self, left: Optional[str], right: Optional[str]) -> Optional[str]:
        def parse(value: Optional[str]) -> tuple[int, int, int] | None:
            if not value:
                return None
            match = DATE_RE.search(value)
            if not match:
                return None
            day, month, year = match.group(1).split("/")
            return int(year), int(month), int(day)

        left_parsed = parse(left)
        right_parsed = parse(right)

        if left_parsed and right_parsed:
            return right if right_parsed > left_parsed else left
        return left or right

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
            "vacantes inicio curso",
            "vacants inici de curs",
            "listado actualizado de vacantes",
            "llistat actualitzat de vacants",
            "lis_vac_act_adj_ini",
            "vac_act_adj_ini",

            # Inicio de curso: adjudicaciones de interinos publicadas sin "_lis_" en el nombre.
            "adjudicaciones inicio de curso",
            "adjudicacions inici de curs",
            "adjudicacion de personal docente inicio de curso",
            "adjudicacio de personal docent inici de curs",
            "ini_2026_adj_int",
            "adj_int_mae",
            "adj_int_sec",
            "adj_int_",

            "dificil cobertura",
            "dificil provisio",
            "dificil provision",
            "puesto asignado provisionalmente",
            "lloc assignat provisionalment",
            # Inicio de curso: listados de participantes/bolsa publicados antes de plazas/adjudicaciones.
            "participantes",
            "participants",
            "listado provisional",
            "llistat provisional",
            "llista provisional",
            "relacion provisional",
            "relacio provisional",
            "personal funcionario interino participante",
            "personal funcionari interi participant",
            "bolsa del cuerpo de maestros",
            "borsa del cos de mestres",
            "bolsas de especialidades",
            "borses d'especialitats",
            "par_pro_int_lis",
            "par_pro_int_res",
            "ini_2026_par_pro",
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
        optional_seed_urls: set[str] | None = None,
    ) -> list[DiscoveredAsset]:
        self.discovery_errors.clear()
        optional_seed_urls = optional_seed_urls or set()
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
                if not any(error.get("url") == current_url for error in self.discovery_errors):
                    self.discovery_errors.append({"url": current_url, "kind": "empty_response", "status_code": None})
                if depth == 0 and current_url in optional_seed_urls:
                    for error in self.discovery_errors:
                        if error.get("url") == current_url and error.get("status_code") == 404:
                            error["optional_probe"] = True
                continue

            if not self._looks_like_html_response(response):
                continue

            soup = BeautifulSoup(response.text, "html.parser")
            page_base = self._page_base(soup,str(response.url))
            page_title = self._extract_page_title(soup) or self.source_label or self.source_key

            for anchor in soup.find_all("a", href=True):
                href = (anchor.get("href") or "").strip()
                if self._is_ignorable_href(href):
                    continue

                absolute_url = self._absolute_url(href, base_url=page_base)
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

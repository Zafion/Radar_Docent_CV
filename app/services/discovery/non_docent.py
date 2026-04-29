from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


NON_DOCENT_BASE_URL = "https://ceice.gva.es/es/web/inclusioeducativa/personal-no-docent"


@dataclass(frozen=True, slots=True)
class NonDocentSourcePage:
    source_key: str
    staff_group_code: str
    staff_group_name: str
    source_url: str


SUBSTITUTION_SOURCE_PAGES: tuple[NonDocentSourcePage, ...] = (
    NonDocentSourcePage(
        source_key="non_docent_adc_eee",
        staff_group_code="EEE",
        staff_group_name="Personal educador de educación especial",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-eee",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_eei",
        staff_group_code="EEI",
        staff_group_name="Personal educador de educación infantil",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-eei",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_tgei",
        staff_group_code="TGEI",
        staff_group_name="Personal técnico de gestión en Educación Infantil",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-tgei",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_fis",
        staff_group_code="FIS",
        staff_group_name="Personal fisioterapeuta",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-fis",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_ils",
        staff_group_code="ILS",
        staff_group_name="Personal técnico de gestión en interpretación de la lengua de signos",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-ils",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_es",
        staff_group_code="ES",
        staff_group_name="Personal educador social",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-es",
    ),
    NonDocentSourcePage(
        source_key="non_docent_adc_toc",
        staff_group_code="TOC",
        staff_group_name="Personal terapeuta ocupacional",
        source_url=f"{NON_DOCENT_BASE_URL}/adjudicacions-toc",
    ),
)


class NonDocentSubstitutionDiscoveryAdapter(BaseDiscoveryAdapter):
    def __init__(self, page: NonDocentSourcePage, timeout: float = 20.0) -> None:
        super().__init__(timeout=timeout)
        self.page = page
        self.source_key = page.source_key
        self.source_url = page.source_url
        self.source_label = f"No docente ADC - {page.staff_group_name}"

    def discover_assets(self) -> list[DiscoveredAsset]:
        soup = self._get_soup()
        page_title = self._extract_page_title(soup) or self.source_label
        assets_by_url: dict[str, DiscoveredAsset] = {}

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if self._is_ignorable_href(href):
                continue

            absolute_url = self._absolute_url(href)
            if not self._looks_like_downloadable_document(absolute_url):
                continue

            title = self._guess_anchor_title(anchor, absolute_url)
            role = self._classify_adc_asset(title=title, url=absolute_url)
            if role is None:
                continue

            canonical_url = self._canonicalize_url(absolute_url)
            section = self._guess_anchor_section(anchor, page_title)
            staff_section = f"{self.page.staff_group_code} - {self.page.staff_group_name}"
            if section and section != page_title:
                section = f"{staff_section} | {section}"
            else:
                section = staff_section

            discovered = DiscoveredAsset(
                source_key=self.source_key,
                source_url=self.source_url,
                publication_label=page_title,
                publication_date_text=self._guess_anchor_publication_date(anchor),
                asset_role=role,
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

        return list(assets_by_url.values())

    def _classify_adc_asset(self, *, title: str, url: str) -> Optional[str]:
        text = self._normalize_match_text(f"{title} {url}")

        if (
            "listadodefinitivo_adc_edu" in text
            or "adjudicacion definitiva adc" in text
            or "adjudicacio definitiva adc" in text
            or "adjudicación definitiva adc" in text
        ):
            return "non_docent_adc_award_pdf"

        if (
            "adc_edu" in text
            or "adc-edu" in text
            or "convocatoria adc" in text
            or "convocatoria adc-edu" in text
        ):
            return "non_docent_adc_call_pdf"

        return None


class NonDocentBagsDiscoveryAdapter(BaseDiscoveryAdapter):
    source_key = "non_docent_bags"
    source_url = f"{NON_DOCENT_BASE_URL}/borses-ocupacio-temporal"
    source_label = "No docente - Bolsas de empleo temporal"

    def discover_assets(self) -> list[DiscoveredAsset]:
        soup = self._get_soup()
        page_title = self._extract_page_title(soup) or self.source_label
        assets_by_url: dict[str, DiscoveredAsset] = {}

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if self._is_ignorable_href(href):
                continue

            title = self._guess_anchor_title(anchor, self._absolute_url(href))
            normalized_title = self._normalize_match_text(title)
            absolute_url = self._absolute_url(href)
            parsed = urlparse(absolute_url)

            if self._is_monthly_update_anchor(normalized_title, absolute_url):
                if not self._looks_like_downloadable_document(absolute_url):
                    continue

                asset = self._build_asset_from_anchor(
                    anchor=anchor,
                    absolute_url=absolute_url,
                    page_title=page_title,
                    asset_role="non_docent_bag_update_pdf",
                )
                assets_by_url[asset.canonical_url] = self._merge_if_exists(
                    assets_by_url.get(asset.canonical_url),
                    asset,
                )
                continue

            if self._is_funcion_publica_bag_link(normalized_title, parsed):
                for asset in self._discover_funcion_publica_detail_pdfs(
                    detail_url=absolute_url,
                    parent_anchor=anchor,
                    parent_title=title,
                    page_title=page_title,
                ):
                    assets_by_url[asset.canonical_url] = self._merge_if_exists(
                        assets_by_url.get(asset.canonical_url),
                        asset,
                    )

        return list(assets_by_url.values())

    def _is_monthly_update_anchor(self, normalized_title: str, url: str) -> bool:
        return (
            ("actualizacion" in normalized_title or "actualitzacio" in normalized_title)
            and self._looks_like_downloadable_document(url)
        )

    def _is_funcion_publica_bag_link(self, normalized_title: str, parsed_url) -> bool:
        if parsed_url.netloc.lower() not in {"sede.gva.es", "www.gva.es"}:
            return False

        return (
            re.search(r"\bbolsa\s+\d{3}[- ]?[a-z]\b", normalized_title) is not None
            or re.search(r"\bborsa\s+\d{3}[- ]?[a-z]\b", normalized_title) is not None
            or "detall-ocupacio-publica" in parsed_url.path.lower()
            or "id_emp=" in parsed_url.query.lower()
        )

    def _discover_funcion_publica_detail_pdfs(
        self,
        *,
        detail_url: str,
        parent_anchor: Tag,
        parent_title: str,
        page_title: str,
    ) -> list[DiscoveredAsset]:
        response = self._fetch_response(detail_url)
        if response is None or not self._looks_like_html_response(response):
            return []

        soup = BeautifulSoup(response.text, "html.parser")
        detail_page_title = self._extract_page_title(soup) or parent_title
        assets: list[DiscoveredAsset] = []

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if self._is_ignorable_href(href):
                continue

            absolute_url = self._absolute_url(href, base_url=str(response.url))
            if not self._looks_like_downloadable_document(absolute_url):
                continue

            title = self._guess_anchor_title(anchor, absolute_url)
            combined = self._normalize_match_text(f"{title} {absolute_url}")

            if not self._looks_like_current_bag_pdf(combined):
                continue

            section = self._guess_bag_section(parent_anchor, page_title)
            if section:
                section = f"{section} | {parent_title}"
            else:
                section = parent_title

            assets.append(
                DiscoveredAsset(
                    source_key=self.source_key,
                    source_url=self.source_url,
                    publication_label=detail_page_title,
                    publication_date_text=self._guess_anchor_publication_date(anchor),
                    asset_role="non_docent_funcion_publica_bag_pdf",
                    title=title,
                    url=absolute_url,
                    canonical_url=self._canonicalize_url(absolute_url),
                    section=section,
                    downloadable=True,
                )
            )

        return assets

    def _looks_like_current_bag_pdf(self, normalized_text: str) -> bool:
        positives = (
            "listado definitivo",
            "llista definitiva",
            "llistat definitiu",
            "listado bolsa",
            "llistat bolsa",
            "llistat d'aprovats",
            "listado aprobados",
            "listadoaprobados",
            "ldefinitiva",
            "llista2correc",
        )
        negatives = (
            "solicitud",
            "instancia",
            "subsanacion",
            "baremacion provisional",
            "baremacion definitiva",
            "tribunal",
            "nombramiento",
            "convocatoria",
            "bases",
        )
        return any(item in normalized_text for item in positives) and not any(
            item in normalized_text for item in negatives
        )

    def _build_asset_from_anchor(
        self,
        *,
        anchor: Tag,
        absolute_url: str,
        page_title: str,
        asset_role: str,
    ) -> DiscoveredAsset:
        title = self._guess_anchor_title(anchor, absolute_url)
        section = self._guess_bag_section(anchor, page_title)
        return DiscoveredAsset(
            source_key=self.source_key,
            source_url=self.source_url,
            publication_label=page_title,
            publication_date_text=self._guess_anchor_publication_date(anchor),
            asset_role=asset_role,
            title=title,
            url=absolute_url,
            canonical_url=self._canonicalize_url(absolute_url),
            section=section,
            downloadable=True,
        )

    def _guess_bag_section(self, anchor: Tag, page_title: str) -> Optional[str]:
        row = anchor.find_parent("tr")
        if row:
            cells = row.find_all(["th", "td"])
            if len(cells) >= 2:
                first_cell_text = self._clean_text(cells[0].get_text(" ", strip=True))
                if first_cell_text:
                    return first_cell_text

        for parent in anchor.parents:
            if not isinstance(parent, Tag):
                continue
            if parent.name in {"table", "section", "article", "div"}:
                heading = parent.find_previous(["h2", "h3", "h4", "strong", "b"])
                if heading:
                    text = self._clean_text(heading.get_text(" ", strip=True))
                    if text:
                        return text

        return self._guess_anchor_section(anchor, page_title)

    def _merge_if_exists(
        self,
        existing: DiscoveredAsset | None,
        incoming: DiscoveredAsset,
    ) -> DiscoveredAsset:
        if existing is None:
            return incoming
        return self._merge_asset(existing, incoming)


def get_non_docent_discovery_adapters() -> list[BaseDiscoveryAdapter]:
    adapters: list[BaseDiscoveryAdapter] = [
        NonDocentSubstitutionDiscoveryAdapter(page)
        for page in SUBSTITUTION_SOURCE_PAGES
    ]
    adapters.append(NonDocentBagsDiscoveryAdapter())
    return adapters

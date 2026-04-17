from __future__ import annotations

import re

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


class ResolucionAdapter(BaseDiscoveryAdapter):
    source_key = "resolucion"
    source_url = "https://ceice.gva.es/es/web/rrhh-educacion/resolucion"
    source_label = "Adjudicación continua / sustituciones"

    def discover_assets(self) -> list[DiscoveredAsset]:
        soup = self._get_soup()
        page_text = self._clean_text(soup.get_text(" ", strip=True))

        publication_date = self._extract_publication_date(page_text)

        results: list[DiscoveredAsset] = []
        seen: set[tuple[str, str]] = set()

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            title = self._clean_text(anchor.get_text(" ", strip=True))
            if not title:
                continue

            asset_role = self._classify_asset_role(title)
            if asset_role is None:
                continue

            absolute_url = self._absolute_url(href)
            canonical_url = self._canonicalize_url(absolute_url)
            downloadable = asset_role != "credencial_link" and self._looks_like_downloadable_document(
                absolute_url
            )

            dedupe_key = (asset_role, canonical_url)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            results.append(
                DiscoveredAsset(
                    source_key=self.source_key,
                    source_url=self.source_url,
                    publication_label="Adjudicación continua / sustituciones",
                    publication_date_text=publication_date,
                    asset_role=asset_role,
                    title=title,
                    url=absolute_url,
                    canonical_url=canonical_url,
                    section=self._find_previous_heading(anchor),
                    downloadable=downloadable,
                )
            )

        return results

    def _extract_publication_date(self, page_text: str) -> str | None:
        match = re.search(
            r"Resolución de adjudicación:\s*([0-9]{2}/[0-9]{2}/[0-9]{4})",
            page_text,
            flags=re.IGNORECASE,
        )
        return match.group(1) if match else None

    def _classify_asset_role(self, title: str) -> str | None:
        text = title.lower()

        if "credencial" in text:
            return "credencial_link"

        if "puestos" in text and "ofertados" in text:
            return "puestos_pdf"

        if "listado" in text and "maestros" in text:
            return "listado_maestros_pdf"

        if "listado" in text and "secundaria" in text:
            return "listado_secundaria_pdf"

        if "resolución" in text or "resolucion" in text:
            return "resolucion_pdf"

        return None
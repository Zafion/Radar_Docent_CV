from __future__ import annotations

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


class Adjudicacion3Adapter(BaseDiscoveryAdapter):
    source_key = "adjudicacion3"
    source_url = "https://ceice.gva.es/es/web/rrhh-educacion/adjudicacion3"

    allowed_sections = (
        "Cuerpo de Maestros",
        "Cuerpos de Secundaria y Otros Cuerpos",
    )

    def discover_assets(self) -> list[DiscoveredAsset]:
        soup = self._get_soup()
        results: list[DiscoveredAsset] = []
        seen: set[tuple[str, str, str]] = set()

        for anchor in soup.find_all("a", href=True):
            href = (anchor.get("href") or "").strip()
            if not href:
                continue

            title = self._clean_text(anchor.get_text(" ", strip=True))
            section = self._find_previous_heading(anchor)

            if section not in self.allowed_sections:
                continue

            absolute_url = self._absolute_url(href)
            canonical_url = self._canonicalize_url(absolute_url)

            asset_role = self._classify_asset_role(title=title, section=section)
            if asset_role is None:
                continue

            dedupe_key = (section or "", title, canonical_url)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            results.append(
                DiscoveredAsset(
                    source_key=self.source_key,
                    source_url=self.source_url,
                    publication_label="Adjudicación inicio de curso",
                    publication_date_text=None,
                    asset_role=asset_role,
                    title=title,
                    url=absolute_url,
                    canonical_url=canonical_url,
                    section=section,
                    downloadable=True,
                )
            )

        return results

    def _classify_asset_role(self, title: str, section: str | None) -> str | None:
        text = title.lower()
        section_text = (section or "").lower()

        if "resolución" in text or "resolucion" in text:
            return "resolucion_pdf"

        if "listado" in text and "maestros" in section_text:
            return "listado_maestros_pdf"

        if "listado" in text and "secundaria" in section_text:
            return "listado_secundaria_pdf"

        return None
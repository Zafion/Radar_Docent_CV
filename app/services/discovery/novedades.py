from __future__ import annotations

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


class NovedadesDiscoveryAdapter(BaseDiscoveryAdapter):
    source_key = "rrhh_novedades"
    source_url = "https://ceice.gva.es/es/web/rrhh-educacion/novedades"
    source_label = "Novedades RRHH Educación"

    def discover_assets(self) -> list[DiscoveredAsset]:
        return self._crawl_seed_urls(
            seed_urls=[self.source_url],
            max_depth=3,
            asset_role="pdf_candidate",
        )
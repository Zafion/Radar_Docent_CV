from __future__ import annotations

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


class RrhhMainDiscoveryAdapter(BaseDiscoveryAdapter):
    source_key = "rrhh_main"
    source_url = "https://ceice.gva.es/es/web/rrhh-educacion"
    source_label = "RRHH Educación"

    def discover_assets(self) -> list[DiscoveredAsset]:
        return self._crawl_seed_urls(
            seed_urls=[self.source_url],
            max_depth=2,
            asset_role="pdf_candidate",
        )
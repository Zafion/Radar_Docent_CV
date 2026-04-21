from __future__ import annotations

from app.services.discovery.base import BaseDiscoveryAdapter, DiscoveredAsset


class GenericFamilyDiscoveryAdapter(BaseDiscoveryAdapter):
    def __init__(
        self,
        *,
        source_key: str,
        base_slug: str,
        source_label: str,
        max_suffix: int = 9,
        max_depth: int = 2,
        timeout: float = 20.0,
    ) -> None:
        super().__init__(timeout=timeout)
        self.source_key = source_key
        self.base_slug = base_slug
        self.source_label = source_label
        self.max_suffix = max_suffix
        self.max_depth = max_depth
        self.source_url = f"https://ceice.gva.es/es/web/rrhh-educacion/{base_slug}"

    def discover_assets(self) -> list[DiscoveredAsset]:
        seed_urls = [self.source_url]
        seed_urls.extend(
            f"{self.source_url}{index}"
            for index in range(1, self.max_suffix + 1)
        )

        return self._crawl_seed_urls(
            seed_urls=seed_urls,
            max_depth=self.max_depth,
            asset_role="pdf_candidate",
        )
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag


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

    def __init__(self, timeout: float = 20.0) -> None:
        self.timeout = timeout
        self.headers = {
            "User-Agent": "RadarDocentCV/0.2 (+https://ceice.gva.es/)"
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

    def _absolute_url(self, href: str) -> str:
        return urljoin(self.source_url, href)

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
        )
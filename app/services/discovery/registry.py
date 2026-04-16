from __future__ import annotations

from app.services.discovery.adjudicacion3 import Adjudicacion3Adapter
from app.services.discovery.resolucion import ResolucionAdapter
from app.services.discovery.resolucion1 import Resolucion1Adapter
from app.services.discovery.base import BaseDiscoveryAdapter


def get_discovery_adapters() -> list[BaseDiscoveryAdapter]:
    return [
        Adjudicacion3Adapter(),
        ResolucionAdapter(),
        Resolucion1Adapter(),
    ]
from __future__ import annotations

from app.services.discovery.base import BaseDiscoveryAdapter
from app.services.discovery.generic_family import GenericFamilyDiscoveryAdapter
from app.services.discovery.novedades import NovedadesDiscoveryAdapter
from app.services.discovery.rrhh_main import RrhhMainDiscoveryAdapter


def get_discovery_adapters() -> list[BaseDiscoveryAdapter]:
    return [
        NovedadesDiscoveryAdapter(),
        RrhhMainDiscoveryAdapter(),
        GenericFamilyDiscoveryAdapter(
            source_key="family_resolucion",
            base_slug="resolucion",
            source_label="Familia resolucion*",
            max_suffix=9,
            max_depth=2,
        ),
        GenericFamilyDiscoveryAdapter(
            source_key="family_adjudicacion",
            base_slug="adjudicacion",
            source_label="Familia adjudicacion*",
            max_suffix=9,
            max_depth=2,
        ),
        GenericFamilyDiscoveryAdapter(
            source_key="family_informacion_documentacion",
            base_slug="informacion-y-documentacion",
            source_label="Familia informacion-y-documentacion*",
            max_suffix=9,
            max_depth=2,
        ),
        GenericFamilyDiscoveryAdapter(
            source_key="family_convocatoria_peticion_telematica",
            base_slug="convocatoria-y-peticion-telematica",
            source_label="Familia convocatoria-y-peticion-telematica*",
            max_suffix=9,
            max_depth=2,
        ),
        GenericFamilyDiscoveryAdapter(
            source_key="family_plazas",
            base_slug="plazas",
            source_label="Familia plazas*",
            max_suffix=9,
            max_depth=2,
        ),
    ]
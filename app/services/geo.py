from __future__ import annotations

from math import radians, sin, cos, sqrt, asin


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Distancia aproximada en kilómetros entre dos coordenadas.
    """
    earth_radius_km = 6371.0088

    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)

    lat1_rad = radians(lat1)
    lat2_rad = radians(lat2)

    a = sin(dlat / 2) ** 2 + cos(lat1_rad) * cos(lat2_rad) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return earth_radius_km * c


def build_google_maps_search_url(lat: float, lon: float) -> str:
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def build_google_maps_directions_url(
    destination_lat: float,
    destination_lon: float,
    origin_lat: float | None = None,
    origin_lon: float | None = None,
) -> str:
    if origin_lat is not None and origin_lon is not None:
        return (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={origin_lat},{origin_lon}"
            f"&destination={destination_lat},{destination_lon}"
        )

    return (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={destination_lat},{destination_lon}"
    )
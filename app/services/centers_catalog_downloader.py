from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

EXPORT_URL = "https://xacen-backend.gva.es/xacen-backend/api/v1/informe/ExcelListadoByProvincias"
PAGE_URL = "https://ceice.gva.es/es/web/admision-alumnado/centres-educatius"
CENTER_DETAIL_URL = "https://xacen.gva.es/xacen-frontend/centro?codigoCentro="


class CentersCatalogDownloadError(RuntimeError):
    pass


class CentersCatalogAuthorizationError(CentersCatalogDownloadError):
    pass


def _build_params(cod_provincia: str = "") -> dict[str, str]:
    return {
        "idioma": "es",
        "codProvincia": cod_provincia,
        "urlEntorno": CENTER_DETAIL_URL,
    }


def _build_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/json, text/plain, */*",
        "Authorization": token,
        "Origin": "https://ceice.gva.es",
        "Referer": "https://ceice.gva.es/",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
        ),
    }


def _compute_sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_previous_sha256(sha_path: Path) -> str | None:
    if not sha_path.exists():
        return None
    value = sha_path.read_text(encoding="utf-8").strip()
    return value or None


def download_centers_catalog(
    *,
    token: str,
    raw_dir: str | Path,
    cod_provincia: str = "",
    timeout_seconds: int = 60,
) -> dict[str, Any]:
    raw_dir = Path(raw_dir)
    raw_dir.mkdir(parents=True, exist_ok=True)

    params = _build_params(cod_provincia)
    headers = _build_headers(token)

    with httpx.Client(timeout=timeout_seconds, follow_redirects=True) as client:
        resp = client.get(EXPORT_URL, params=params, headers=headers)

    if resp.status_code in (401, 403):
        raise CentersCatalogAuthorizationError(
            f"No autorizado al descargar el catálogo de centros ({resp.status_code})"
        )

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise CentersCatalogDownloadError(str(exc)) from exc

    try:
        payload = resp.json()
    except json.JSONDecodeError as exc:
        raise CentersCatalogDownloadError("La respuesta del endpoint no es JSON válido") from exc

    if "contenidoBase64" not in payload:
        raise CentersCatalogDownloadError(f"Respuesta inesperada del endpoint: {payload}")

    try:
        file_bytes = base64.b64decode(payload["contenidoBase64"])
    except Exception as exc:  # noqa: BLE001
        raise CentersCatalogDownloadError("No se pudo decodificar contenidoBase64") from exc

    if len(file_bytes) < 1024:
        raise CentersCatalogDownloadError("El Excel decodificado es demasiado pequeño para ser válido")

    filename = str(payload.get("nombreFichero") or "Listado_Centros_Provincias.xlsx")
    if not filename.lower().endswith(".xlsx"):
        filename = "Listado_Centros_Provincias.xlsx"

    response_json_path = raw_dir / "respuesta_centros.json"
    xlsx_path = raw_dir / filename
    sha_path = raw_dir / "sha256.txt"

    response_json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    xlsx_path.write_bytes(file_bytes)

    sha256_value = _compute_sha256(file_bytes)
    previous_sha256 = _read_previous_sha256(sha_path)
    changed = sha256_value != previous_sha256

    sha_path.write_text(sha256_value + "\n", encoding="utf-8")

    return {
        "source_page_url": PAGE_URL,
        "source_api_url": EXPORT_URL,
        "cod_provincia": cod_provincia,
        "response_filename": filename,
        "response_mime_type": payload.get("mimeType"),
        "response_json_path": str(response_json_path),
        "xlsx_path": str(xlsx_path),
        "sha256": sha256_value,
        "previous_sha256": previous_sha256,
        "changed": changed,
    }

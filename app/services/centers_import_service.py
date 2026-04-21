from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from app.storage.centers_store import count_centers, upsert_centers

EXPECTED_COLUMNS = {
    "Código",
    "Denominación Genérica ES",
    "Denominación Genérica VAL",
    "Denominación Específica",
    "Denominación",
    "Régimen",
    "Tipo Vía",
    "Dirección",
    "Número",
    "Código Postal",
    "Localidad",
    "Provincia",
    "Teléfono",
    "Fax",
    "Longitud",
    "Latitud",
    "Comarca",
}


class CentersImportError(RuntimeError):
    pass


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>"}:
        return None
    return text


def _normalize_center_code(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None

    if text.endswith(".0"):
        text = text[:-2]

    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        return None

    return digits.zfill(8)


def _normalize_postal_code(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(5) if digits else text


def _normalize_phone(value: Any) -> str | None:
    text = _clean_text(value)
    if text is None:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _to_float(value: Any) -> float | None:
    text = _clean_text(value)
    if text is None:
        return None
    text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _build_full_address(street_type: str | None, street_name: str | None, street_number: str | None,
                        postal_code: str | None, locality: str | None, province: str | None) -> str | None:
    line_1 = " ".join(part for part in [street_type, street_name, street_number] if part)
    line_2 = " ".join(part for part in [postal_code, locality] if part)
    line_3 = province
    full = ", ".join(part for part in [line_1, line_2, line_3] if part)
    return full or None


def load_centers_from_excel(xlsx_path: str | Path) -> list[dict[str, Any]]:
    path = Path(xlsx_path)
    if not path.exists():
        raise CentersImportError(f"Excel no encontrado: {path}")

    df = pd.read_excel(path, dtype=str, keep_default_na=False)

    missing = EXPECTED_COLUMNS - set(df.columns)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise CentersImportError(f"Faltan columnas obligatorias en el Excel: {missing_text}")

    rows: list[dict[str, Any]] = []
    for record in df.to_dict(orient="records"):
        center_code = _normalize_center_code(record.get("Código"))
        denomination = _clean_text(record.get("Denominación"))

        if not center_code or not denomination:
            continue

        street_type = _clean_text(record.get("Tipo Vía"))
        street_name = _clean_text(record.get("Dirección"))
        street_number = _clean_text(record.get("Número"))
        postal_code = _normalize_postal_code(record.get("Código Postal"))
        locality = _clean_text(record.get("Localidad"))
        province = _clean_text(record.get("Provincia"))

        rows.append(
            {
                "center_code": center_code,
                "denomination": denomination,
                "generic_name_es": _clean_text(record.get("Denominación Genérica ES")),
                "generic_name_val": _clean_text(record.get("Denominación Genérica VAL")),
                "specific_name": _clean_text(record.get("Denominación Específica")),
                "regime": _clean_text(record.get("Régimen")),
                "street_type": street_type,
                "street_name": street_name,
                "street_number": street_number,
                "postal_code": postal_code,
                "locality": locality,
                "province": province,
                "comarca": _clean_text(record.get("Comarca")),
                "phone": _normalize_phone(record.get("Teléfono")),
                "fax": _normalize_phone(record.get("Fax")),
                "longitude": _to_float(record.get("Longitud")),
                "latitude": _to_float(record.get("Latitud")),
                "full_address": _build_full_address(
                    street_type,
                    street_name,
                    street_number,
                    postal_code,
                    locality,
                    province,
                ),
                "source_filename": path.name,
            }
        )

    if not rows:
        raise CentersImportError("No se pudo extraer ningún centro válido del Excel")

    return rows


def import_centers_catalog(db_path: str | Path, xlsx_path: str | Path) -> dict[str, Any]:
    db_path = Path(db_path)
    rows = load_centers_from_excel(xlsx_path)

    with sqlite3.connect(db_path) as conn:
        before = count_centers(conn)
        processed = upsert_centers(conn, rows)
        after = count_centers(conn)
        conn.commit()

    return {
        "db_path": str(db_path),
        "xlsx_path": str(Path(xlsx_path)),
        "processed_rows": processed,
        "centers_before": before,
        "centers_after": after,
    }

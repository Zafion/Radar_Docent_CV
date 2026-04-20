from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping, Any

UPSERT_SQL = """
INSERT INTO centers (
    center_code,
    denomination,
    generic_name_es,
    generic_name_val,
    specific_name,
    regime,
    street_type,
    street_name,
    street_number,
    postal_code,
    locality,
    province,
    comarca,
    phone,
    fax,
    longitude,
    latitude,
    full_address,
    source_filename
) VALUES (
    :center_code,
    :denomination,
    :generic_name_es,
    :generic_name_val,
    :specific_name,
    :regime,
    :street_type,
    :street_name,
    :street_number,
    :postal_code,
    :locality,
    :province,
    :comarca,
    :phone,
    :fax,
    :longitude,
    :latitude,
    :full_address,
    :source_filename
)
ON CONFLICT(center_code) DO UPDATE SET
    denomination = excluded.denomination,
    generic_name_es = excluded.generic_name_es,
    generic_name_val = excluded.generic_name_val,
    specific_name = excluded.specific_name,
    regime = excluded.regime,
    street_type = excluded.street_type,
    street_name = excluded.street_name,
    street_number = excluded.street_number,
    postal_code = excluded.postal_code,
    locality = excluded.locality,
    province = excluded.province,
    comarca = excluded.comarca,
    phone = excluded.phone,
    fax = excluded.fax,
    longitude = excluded.longitude,
    latitude = excluded.latitude,
    full_address = excluded.full_address,
    source_filename = excluded.source_filename,
    updated_at = CURRENT_TIMESTAMP
"""


def upsert_centers(conn: sqlite3.Connection, rows: Iterable[Mapping[str, Any]]) -> int:
    payload = list(rows)
    if not payload:
        return 0

    conn.executemany(UPSERT_SQL, payload)
    return len(payload)


def get_center_by_code(conn: sqlite3.Connection, center_code: str) -> sqlite3.Row | None:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        "SELECT * FROM centers WHERE center_code = ?",
        (center_code,),
    ).fetchone()


def count_centers(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM centers").fetchone()
    return int(row[0])

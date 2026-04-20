from __future__ import annotations

import sqlite3
from typing import Iterable, Mapping, Any, Sequence

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


def get_centers_by_codes(conn: sqlite3.Connection, center_codes: Sequence[str]) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row

    codes = [code for code in center_codes if code]
    if not codes:
        return []

    placeholders = ",".join("?" for _ in codes)
    sql = f"""
        SELECT *
        FROM centers
        WHERE center_code IN ({placeholders})
        ORDER BY province ASC, locality ASC, denomination ASC
    """
    return conn.execute(sql, codes).fetchall()


def search_centers(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row

    text = (query or "").strip()
    if not text:
        return []

    pattern = f"%{text}%"
    return conn.execute(
        """
        SELECT
            center_code,
            denomination,
            generic_name_es,
            generic_name_val,
            specific_name,
            regime,
            full_address,
            postal_code,
            locality,
            province,
            comarca,
            phone,
            fax,
            latitude,
            longitude
        FROM centers
        WHERE center_code = ?
           OR normalize_text(denomination) LIKE normalize_text(?)
           OR normalize_text(COALESCE(generic_name_es, '')) LIKE normalize_text(?)
           OR normalize_text(COALESCE(generic_name_val, '')) LIKE normalize_text(?)
           OR normalize_text(COALESCE(specific_name, '')) LIKE normalize_text(?)
           OR normalize_text(COALESCE(full_address, '')) LIKE normalize_text(?)
           OR normalize_text(COALESCE(locality, '')) LIKE normalize_text(?)
        ORDER BY province ASC, locality ASC, denomination ASC
        LIMIT ?
        """,
        [text, pattern, pattern, pattern, pattern, pattern, pattern, limit],
    ).fetchall()


def count_centers(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) FROM centers").fetchone()
    return int(row[0])
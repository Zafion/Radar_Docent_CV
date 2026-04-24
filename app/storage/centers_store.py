from __future__ import annotations

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
    %(center_code)s,
    %(denomination)s,
    %(generic_name_es)s,
    %(generic_name_val)s,
    %(specific_name)s,
    %(regime)s,
    %(street_type)s,
    %(street_name)s,
    %(street_number)s,
    %(postal_code)s,
    %(locality)s,
    %(province)s,
    %(comarca)s,
    %(phone)s,
    %(fax)s,
    %(longitude)s,
    %(latitude)s,
    %(full_address)s,
    %(source_filename)s
)
ON CONFLICT(center_code) DO UPDATE SET
    denomination = EXCLUDED.denomination,
    generic_name_es = EXCLUDED.generic_name_es,
    generic_name_val = EXCLUDED.generic_name_val,
    specific_name = EXCLUDED.specific_name,
    regime = EXCLUDED.regime,
    street_type = EXCLUDED.street_type,
    street_name = EXCLUDED.street_name,
    street_number = EXCLUDED.street_number,
    postal_code = EXCLUDED.postal_code,
    locality = EXCLUDED.locality,
    province = EXCLUDED.province,
    comarca = EXCLUDED.comarca,
    phone = EXCLUDED.phone,
    fax = EXCLUDED.fax,
    longitude = EXCLUDED.longitude,
    latitude = EXCLUDED.latitude,
    full_address = EXCLUDED.full_address,
    source_filename = EXCLUDED.source_filename,
    updated_at = NOW()
"""


def upsert_centers(conn: Any, rows: Iterable[Mapping[str, Any]]) -> int:
    payload = list(rows)
    if not payload:
        return 0

    cursor = conn._conn.cursor()
    cursor.executemany(UPSERT_SQL, payload)
    return len(payload)


def get_center_by_code(conn: Any, center_code: str) -> dict[str, Any] | None:
    return conn.execute(
        "SELECT * FROM centers WHERE center_code = %s",
        (center_code,),
    ).fetchone()


def count_centers(conn: Any) -> int:
    row = conn.execute("SELECT COUNT(*) FROM centers").fetchone()
    return int(row[0])
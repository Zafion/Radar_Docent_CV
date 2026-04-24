from __future__ import annotations

import os
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def _require_db_url() -> str:
    db_url = os.getenv("RADAR_DOCENT_DB_URL", "").strip()
    if not db_url:
        raise RuntimeError("RADAR_DOCENT_DB_URL is not configured")
    return db_url


_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            conninfo=_require_db_url(),
            min_size=1,
            max_size=10,
            kwargs={
                "autocommit": False,
                "row_factory": dict_row,
            },
            open=True,
        )
    return _pool


def close_pool() -> None:
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_raw_connection() -> psycopg.Connection:
    return psycopg.connect(
        _require_db_url(),
        autocommit=False,
        row_factory=dict_row,
    )


class PgCompatRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return list(self.values())[key]
        return super().__getitem__(key)


class PgCompatCursor:
    def __init__(self, cursor) -> None:
        self._cursor = cursor

    def fetchone(self):
        row = self._cursor.fetchone()
        if row is None:
            return None
        return PgCompatRow(row)

    def fetchall(self):
        return [PgCompatRow(row) for row in self._cursor.fetchall()]

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount


class PgCompatConnection:
    def __init__(self, conn) -> None:
        self._conn = conn

    def execute(self, sql: str, params=None) -> PgCompatCursor:
        sql_pg = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql_pg, params or [])
        return PgCompatCursor(cur)

    def close(self) -> None:
        return None

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()


def get_connection() -> PgCompatConnection:
    return PgCompatConnection(get_raw_connection())


def init_db(schema_path: str | None = None) -> str:
    schema_file = Path(schema_path or "app/storage/schema.sql")
    schema_sql = schema_file.read_text(encoding="utf-8")

    with get_raw_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
        connection.commit()

    return _require_db_url()

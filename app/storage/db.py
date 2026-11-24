from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
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
_write_fence = ContextVar('selective_write_fence', default=None)
_transaction_authorization = ContextVar('selective_transaction_authorization', default=None)


@contextmanager
def selective_transaction_fence(fence, authorization=None):
    """Guard selected synchronous DB operations without changing legacy calls."""
    if fence is None:
        raise ValueError('fence_required')
    token = _write_fence.set(fence)
    authority_token = _transaction_authorization.set(authorization)
    try:
        yield
    finally:
        _transaction_authorization.reset(authority_token)
        _write_fence.reset(token)


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


def _open_connection() -> psycopg.Connection:
    return psycopg.connect(
        _require_db_url(),
        autocommit=False,
        row_factory=dict_row,
    )


def get_raw_connection() -> psycopg.Connection:
    if _transaction_authorization.get() is not None:
        raise RuntimeError('raw_connection_forbidden_in_selective_scope')
    return _open_connection()


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
        self._held_authorization = None

    def _guard(self):
        try:
            fence = _write_fence.get()
            if fence is not None:
                fence.check()
            authorization = _transaction_authorization.get()
            if self._held_authorization is not None and authorization != self._held_authorization:
                raise RuntimeError('transaction_authorization_scope_changed')
            if authorization is not None and self._held_authorization is None:
                authorization.acquire(self._conn)
                self._held_authorization = authorization
        except BaseException:
            self._conn.rollback()
            self._held_authorization = None
            raise

    def execute(self, sql: str, params=None) -> PgCompatCursor:
        self._guard()
        sql_pg = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql_pg, params or [])
        return PgCompatCursor(cur)

    def close(self) -> None:
        self._conn.close()

    def commit(self) -> None:
        self._guard()
        try:
            self._conn.commit()
        finally:
            self._held_authorization = None

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        finally:
            self._held_authorization = None


def get_connection() -> PgCompatConnection:
    return PgCompatConnection(_open_connection())


def init_db(schema_path: str | None = None) -> str:
    schema_file = Path(schema_path or "app/storage/schema.sql")
    schema_sql = schema_file.read_text(encoding="utf-8")

    with get_raw_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(schema_sql)
        connection.commit()

    return _require_db_url()

"""Compare restored databases via read-only ordered row streams, without exporting rows.

Connections use libpq service names supplied by the operator; passwords must not
appear in arguments. A difference is reported rather than interpreted as loss.
"""
import argparse
import hashlib
import json

import psycopg
from psycopg import sql


def snapshot(service):
    db = psycopg.connect(service=service, connect_timeout=5)
    db.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
    db.execute("SET LOCAL statement_timeout = '10min'")
    db.execute("SET LOCAL lock_timeout = '5s'")
    db.execute("SET LOCAL timezone = 'UTC'")
    db.execute("SET LOCAL datestyle = 'ISO, YMD'")
    db.execute("SET LOCAL bytea_output = 'hex'")
    db.execute("SET LOCAL extra_float_digits = 3")
    local, addresses, readonly = db.execute("SELECT inet_server_addr() IS NULL, current_setting('listen_addresses'), current_setting('transaction_read_only')").fetchone()
    if not local or addresses or readonly != 'on':
        db.close()
        raise ValueError('database_not_isolated_unix_readonly')
    if db.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind IN ('r','p') AND c.relrowsecurity").fetchone()[0]:
        db.close()
        raise ValueError('row_level_security_requires_manual_comparison')
    return db


def inventory(db):
    return db.execute("""SELECT c.relname, c.relkind,
       coalesce((SELECT json_agg(json_build_array(a.attname, pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull)
                  ORDER BY a.attnum)::text FROM pg_attribute a
                  WHERE a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped),'[]'),
       coalesce((SELECT array_agg(a.attname ORDER BY k.ordinality)
          FROM pg_index i CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,ordinality)
          JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
          WHERE i.indrelid=c.oid AND i.indisprimary),'{}')
      FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY c.relname""").fetchall()


def digest_table(db, table, keys):
    order = sql.SQL(',').join(sql.Identifier(k) for k in keys) if keys else sql.SQL('to_jsonb(t)::text COLLATE "C"')
    query = sql.SQL('SELECT to_jsonb(t)::text FROM public.{} t ORDER BY {}').format(sql.Identifier(table), order)
    digest = hashlib.sha256()
    count = 0
    with db.cursor(name='comparison_stream') as cur:
        cur.execute(query)
        while batch := cur.fetchmany(500):
            for (row,) in batch:
                value = row.encode('utf-8')
                digest.update(len(value).to_bytes(8, 'big'))
                digest.update(value)
                count += 1
    return {'status': 'compared', 'count': count, 'sha256': digest.hexdigest()}


def difference_counts(left, right, table, keys):
    # Sort key components as text under C collation on both servers, and merge
    # in Python. Primary keys are non-null; no record values leave this function.
    if keys:
        cols = [sql.SQL('(to_jsonb(t)->>{}) COLLATE "C"').format(sql.Literal(k)) for k in keys]
        query = sql.SQL('SELECT {},to_jsonb(t)::text FROM public.{} t ORDER BY {}').format(
            sql.SQL(',').join(cols), sql.Identifier(table), sql.SQL(',').join(cols))
    else:
        query = sql.SQL('SELECT to_jsonb(t)::text FROM public.{} t ORDER BY to_jsonb(t)::text COLLATE "C"').format(sql.Identifier(table))
    def records(db):
        with db.cursor(name='difference_stream') as cur:
            cur.execute(query)
            while batch := cur.fetchmany(500):
                for record in batch:
                    yield (tuple(record[:-1]), record[-1]) if keys else ((record[0],), record[0])
    a, b = iter(records(left)), iter(records(right))
    x, y = next(a, None), next(b, None)
    only_left = only_right = changed = 0
    while x is not None or y is not None:
        if y is None or (x is not None and x[0] < y[0]):
            only_left += 1; x = next(a, None)
        elif x is None or y[0] < x[0]:
            only_right += 1; y = next(b, None)
        else:
            changed += x[1] != y[1]
            x, y = next(a, None), next(b, None)
    return {'only_left': only_left, 'only_right': only_right, 'changed_same_primary_key': changed if keys else None}


def compare(left, right):
    li = {r[0]: r[1:] for r in inventory(left)}
    ri = {r[0]: r[1:] for r in inventory(right)}
    if not li or not ri:
        raise ValueError('empty_public_inventory')
    tables = {}
    for name in sorted(li.keys() | ri.keys()):
        if name not in li or name not in ri:
            tables[name] = {'status': 'table_only_one_side'}
        elif li[name] != ri[name]:
            tables[name] = {'status': 'schema_or_primary_key_differs'}
        else:
            a, b = digest_table(left, name, li[name][2]), digest_table(right, name, ri[name][2])
            tables[name] = {'status': 'equal' if a['status'] == b['status'] == 'compared' and a == b
                            else 'not_comparable' if a['status'] != 'compared' or b['status'] != 'compared'
                            else 'rows_differ', 'left_rows': a.get('count'), 'right_rows': b.get('count'),
                            'left_sha256': a.get('sha256'), 'right_sha256': b.get('sha256')}
            if tables[name]['status'] == 'rows_differ':
                tables[name]['differences'] = difference_counts(left, right, name, li[name][2])
    return {'status': 'equal' if all(t['status'] == 'equal' for t in tables.values()) else 'differences_or_unverified',
            'method': 'public table rows ordered by primary key or full-row multiset; all columns; sha256 stream', 'tables': tables,
            'limitations': 'Does not compare functions, triggers, privileges, sequences, non-public schemas or physical bytes; different internal keys remain differences.'}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--left-service', required=True)
    p.add_argument('--right-service', required=True)
    a = p.parse_args()
    if a.left_service == a.right_service:
        raise SystemExit('services_must_differ')
    with snapshot(a.left_service) as left, snapshot(a.right_service) as right:
        print(json.dumps(compare(left, right), indent=2, sort_keys=True))


if __name__ == '__main__':
    main()

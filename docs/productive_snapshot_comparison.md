# Productive snapshot: manual procedure, pending independent authorization

Do not execute this procedure merely because it is documented. The application
unit in the local repository names `/etc/funkcionario/funkcionario.env`, and
`app/storage/db.py` reads `RADAR_DOCENT_DB_URL`. The server's actual unit and
process may differ. This procedure does not change services, roles, sudoers,
authentication or data. It does not authorize a deployment.

## Check the active web process on the server

Run manually as an administrator; this prints only database, host and port for
a simple PostgreSQL URI, never the user or password. It stops for a missing
environment entry, libpq keyword DSN, service or URI query parameters, which
could override the effective connection. It reads the running process's
environment in memory; do not print or save that environment.

```bash
pid=$(systemctl show funkcionario-web.service -p MainPID --value)
sudo python3 - "$pid" <<'PY'
import sys
from urllib.parse import urlsplit, unquote
pid = int(sys.argv[1])
if pid <= 1:
    raise SystemExit('STOP: no active web process')
items = open(f'/proc/{pid}/environ', 'rb').read().split(b'\0')
vals = [v.split(b'=', 1)[1] for v in items if v.startswith(b'RADAR_DOCENT_DB_URL=')]
if len(vals) != 1:
    raise SystemExit('STOP: connection setting missing or ambiguous')
u = urlsplit(vals[0].decode())
if u.scheme not in ('postgres', 'postgresql') or u.query or u.fragment or not u.path.strip('/'):
    raise SystemExit('STOP: connection format requires private administrative review')
print('database:', unquote(u.path.lstrip('/')))
print('host:', u.hostname or '(local socket)')
print('port:', u.port or 5432)
PY
```

Confirm privately that the connection targets this PostgreSQL 16 cluster and
`radar_docent_cv`; a database name alone does not prove the server. Environment
inspection does not prove that a different process or dynamic configuration is
not in use. Stop if ambiguous. The existing administrative access is not to
be used as the application's database identity.

## Acquire, after a separate authorization

The local script uses the pinned key and strict host checking already documented
for `funkcionario@178.104.237.94`. Its sole remote command is:

```text
sudo -n -u postgres /usr/lib/postgresql/16/bin/pg_dump -w --format=custom --lock-wait-timeout=5s --dbname=radar_docent_cv
```

This requires the existing SSH identity and existing noninteractive sudo for
that exact command. They have not been verified for this new operation. It does
not fall back to passwords or change sudoers. If sudo requires a TTY or a
password, stop; an administrator must arrange a separately approved transfer
method. Do not allocate an SSH pseudo-terminal for binary dump data.

On the local workstation, once authorized and the LUKS volume is mounted:

```bash
cd /home/zafion/Proyects/Radar_Docent_CV
.venv/bin/python -B scripts/acquire_productive_snapshot_manual.py
```

The script checks LUKS2, ownership, permissions and 2 GiB free initially,
preserves a 1 GiB margin during streaming, exclusively creates a private
directory, checks the dump size, computes and rereads SHA-256, and checks the
custom archive table of contents with PostgreSQL 16 `pg_restore --list`. Its
receipt includes no credentials or rows. It creates no remote dump file.
SSH provides transport integrity; the SHA is computed on the received stream
and checked against its local reread, not independently on a remote file.
TOC readability does not verify every archive block; restoration is required.
Interrupted or failed runs do not become verified snapshots. pg_dump reads the
whole database and takes ACCESS SHARE locks; it can affect I/O and conflict with
DDL. The still-enabled backup timer may compete for I/O. Monitor capacity and
cancel rather than changing services without authorization. A pg_dump snapshot
is consistent for transactional tables; sequence values are not MVCC snapshots.

## Restore and compare locally

The current LUKS volume had approximately 12 GiB free when checked. The
existing restore preflight requires **20 GiB free**; do not lower that check.
Arrange additional capacity without deleting protected clusters before the
following command. The exact snapshot directory is printed by acquisition.
Replace only the placeholder with that path inside `recovery_19nov_capacity`:

```bash
.venv/bin/python -B scripts/restore_productive_snapshot_local.py \
  /home/zafion/Proyects/Radar_Docent_CV/auditoria_local/recovery_19nov_capacity/SNAPSHOT_DIRECTORY
```

The script checks the receipt, SHA and private permissions, then uses the
existing isolated PostgreSQL 16 restore worker; it produces `restore.json` in
the snapshot directory with the new `work_directory`. Do not substitute the
21 November cluster for the new one. After the restore is stopped, run:

```bash
.venv/bin/python -B scripts/compare_productive_snapshot_local.py \
  /home/zafion/Proyects/Radar_Docent_CV/auditoria_local/recovery_19nov_capacity/funk-baseline-v3jm9so3 \
  /home/zafion/Proyects/Radar_Docent_CV/auditoria_local/recovery_19nov_capacity/NEW_WORK_DIRECTORY
```

Replace `NEW_WORK_DIRECTORY` with the `work_directory` from `restore.json`.
Both clusters must be stopped, separate and on the verified LUKS volume. The
wrapper starts temporary copy-on-write overlays in a network-isolated namespace;
it does not write to the original clusters. Its JSON reports public table
inventories, column/primary-key differences and ordered SHA-256 streams of all
row fields. A table without a primary key is unverified, not equal. IDs that
differ are reported as differences; this is deliberately not person-name
matching. Tables outside `public`, sequence values, privileges, functions,
triggers and constraints are not certified by that row comparison. Reconcile
any differences by documentary identity on a separately authorized isolated
clone, never by silently replacing production. Store the aggregate JSON only
inside LUKS if a persistent receipt is needed.

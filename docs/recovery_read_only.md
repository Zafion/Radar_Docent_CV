# Read-only recovery preparation

This is an operator procedure, not permission to connect to or modify a server.
The isolated November 21 archive is a verified local baseline, not evidence of
the latest production state. The application must not be reopened until a
separately authorized production baseline comparison and deployment rehearsal
have succeeded.

## Identify the database and a read-only identity

An administrator should confirm privately which database and socket or host
the stopped application actually uses. Historical database and role names are
not proof of current configuration. Do not copy passwords, connection strings
containing secrets, or personal data into receipts or chat.

Configure or identify a protected libpq service named `funk_recovery_readonly`
only under separate authorization. Its principal needs `CONNECT`, schema
`USAGE`, and `SELECT` on the required tables and catalog views, with no write,
role-creation, superuser, replication, or bypass-RLS privileges. Document RLS
coverage separately. Grant or authentication changes require their own
authorization.

After explicit authorization to query the verified database, an administrator
can run the following using the already configured service and no interactive
password prompt. Failure is a stop condition, not a reason to try other roles.

```bash
PGSERVICE=funk_recovery_readonly psql -X -w -v ON_ERROR_STOP=1 <<'SQL'
BEGIN TRANSACTION READ ONLY;
SET LOCAL statement_timeout = '30s';
SELECT current_database(), current_user, current_setting('server_version'),
       current_setting('transaction_read_only');
SELECT d.datname, has_database_privilege(current_user,d.oid,'CONNECT') AS can_connect
  FROM pg_database d WHERE d.datallowconn ORDER BY d.datname;
SELECT count(*) AS documents FROM public.documents;
SELECT count(*) AS document_versions FROM public.document_versions;
SELECT count(*) AS award_results FROM public.award_results;
SELECT count(*) AS award_assignments FROM public.award_assignments;
SELECT count(*) AS offered_positions FROM public.offered_positions;
SELECT count(*) AS bag_members FROM public.docent_bag_members;
SELECT count(*) AS difficult_positions FROM public.difficult_coverage_positions;
SELECT count(*) AS difficult_candidates FROM public.difficult_coverage_candidates;
SELECT count(*) AS alerts FROM public.public_alerts;
SELECT count(*) AS posts FROM public.notification_posts;
SELECT count(*) AS push_events FROM public.push_notification_events;
SELECT max(finished_at) AS latest_parser_finish FROM public.document_parse_runs;
SELECT max(downloaded_at) AS latest_document_download FROM public.document_versions;
ROLLBACK;
SQL
```

Only aggregate metadata is returned. Matching counts and timestamps do not
prove row equality or that processing stopped. If newer or unverified data may
exist, acquire a consistent backup only under a separate explicit approval.
Read-only `pg_dump` still causes database I/O and obtains `ACCESS SHARE` locks;
verify privileges, RLS, available encrypted storage, source identity, transfer
integrity and restore viability before using the backup. Compare an isolated
restore by document SHA and stable keys, retaining the original and ambiguous
rows; do not overwrite production from a historical snapshot.

## Future read-only opening

The example `deploy/funkcionario-recovery-consulta.service.example` and
`scripts/start_recovery_readonly.sh` are not installed automatically. The API
requires `FUNK_RECOVERY_READ_ONLY=1`, `ENABLE_NOTIFICATIONS=0`, a PostgreSQL
role without write authority, a private Unix socket, and egress restrictions.
The recovery web unit does **not** disable other services: independently stop
and verify pipeline and center-sync timers, cron, workers, matcher, scrapers,
mail, Telegram, push and all producers. Their eventual reactivation needs a
separate approval.

Before any opening, take a verified pre-change backup; rehearse migrations and
incremental reconciliation against the actual baseline on an isolated clone;
verify the API, browser, counts, provenance and quarantine; test full rollback;
and document cancellation criteria. Only then request separate deployment and
opening authorization. On failure, keep services stopped and restore the
verified pre-change backup using the approved rollback procedure.

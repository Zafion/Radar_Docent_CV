#!/usr/bin/env bash
# The recovery web process only; does not start or control any workers or timers.
set -euo pipefail
export FUNK_RECOVERY_READ_ONLY=1
export ENABLE_NOTIFICATIONS=0
: "${RADAR_DOCENT_DB_URL:?read-only PostgreSQL connection required}"
exec .venv/bin/python -B -m uvicorn app.api.main:app --uds /run/funkcionario-recovery/web.sock --no-access-log

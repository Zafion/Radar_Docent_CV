#!/usr/bin/env bash
# Independent recovery web instance. Scheduling and producer services are separate.
set -euo pipefail
export FUNK_RECOVERY_READ_ONLY=1
export ENABLE_NOTIFICATIONS=0
: "${RADAR_DOCENT_DB_URL:?read-only PostgreSQL connection required}"
exec .venv/bin/python -B -m uvicorn app.api.main:app \
  --host 127.0.0.1 --port 8001 --no-access-log --log-level critical

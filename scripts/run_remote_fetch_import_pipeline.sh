#!/usr/bin/env bash
set -Eeuo pipefail

LOCK_FILE="/tmp/funkcionario-remote-fetch-import.lock"
LOG_DIR="/srv/funkcionario/remote_fetch/logs"
LOG_FILE="$LOG_DIR/import-$(date +'%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another remote fetch import is already running."
  exit 0
fi

{
  echo "============================================================"
  echo "REMOTE FETCH IMPORT PIPELINE"
  echo "Started at: $(date -Is)"
  echo "Host: $(hostname)"
  echo "============================================================"

  cd /srv/funkcionario/app

  source .venv/bin/activate

  set -a
  source /etc/funkcionario/funkcionario.env
  set +a

  echo
  echo "1) Import remote fetch"
  python scripts/import_remote_fetch.py

  echo
  echo "2) Register documents"
  python run_register_documents.py

  echo
  echo "3) Parse documents"
  python run_parse_documents.py

  echo
  echo "4) Update position lifecycle"
  python run_update_position_lifecycle.py

  echo
  echo "============================================================"
  echo "Finished at: $(date -Is)"
  echo "OK"
} 2>&1 | tee "$LOG_FILE"

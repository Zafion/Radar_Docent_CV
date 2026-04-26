#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv/bin/activate"
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE="/tmp/radar_docent_cv_pipeline.lock"
ENV_FILE="${RADAR_ENV_FILE:-/etc/funkcionario/funkcionario.env}"
LOCAL_ENV_FILE="$PROJECT_DIR/env.local.sh"

mkdir -p "$LOG_DIR"

if [[ -f "$LOCAL_ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$LOCAL_ENV_FILE"
  set +a
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

: "${RADAR_DOCENT_DB_URL:?RADAR_DOCENT_DB_URL is required}"

exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "$(date '+%F %T') Pipeline ya en ejecución, salgo."
  exit 0
}

cd "$PROJECT_DIR"
source "$VENV"

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

echo "============================================================"
echo "Inicio pipeline: $(date '+%F %T')"

python run_sync_all.py
python run_register_documents.py
python run_parse_documents.py

echo "Fin pipeline: $(date '+%F %T')"

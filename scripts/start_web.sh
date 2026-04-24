#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$PROJECT_DIR/.venv/bin/activate"
ENV_FILE="${RADAR_ENV_FILE:-/etc/funckcionario/funckcionario.env}"
LOCAL_ENV_FILE="$PROJECT_DIR/env.local.sh"

cd "$PROJECT_DIR"
source "$VENV"

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

export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

exec uvicorn app.api.main:app       --host "${UVICORN_HOST:-127.0.0.1}"       --port "${UVICORN_PORT:-8000}"

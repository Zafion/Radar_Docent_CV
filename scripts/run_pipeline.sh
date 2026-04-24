#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="/home/zafion/Proyects/Radar_Docent_CV"
VENV="$PROJECT_DIR/.venv/bin/activate"
LOG_DIR="$PROJECT_DIR/logs"
LOCK_FILE="/tmp/radar_docent_cv_pipeline.lock"

mkdir -p "$LOG_DIR"

exec 9>"$LOCK_FILE"
flock -n 9 || {
  echo "$(date '+%F %T') Pipeline ya en ejecución, salgo."
  exit 0
}

cd "$PROJECT_DIR"
source "$VENV"

export RADAR_DOCENT_DB_URL="postgresql://radar_docent:TU_PASSWORD@localhost:5432/radar_docent_cv"
export PYTHONUNBUFFERED=1

echo "============================================================"
echo "Inicio pipeline: $(date '+%F %T')"

python run_sync_all.py
python run_register_documents.py
python run_parse_documents.py

echo "Fin pipeline: $(date '+%F %T')"
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

export RADAR_DOCENT_DB_PATH="$PROJECT_DIR/data/radar_docent_cv.db"
export PYTHONUNBUFFERED=1

echo "============================================================"
echo "Inicio pipeline: $(date '+%F %T')"

python run_sync_all.py
python run_register_documents.py
python run_parse_offered_positions.py
python run_parse_award_results_maestros.py
python run_parse_award_results_secundaria.py
python run_parse_difficult_coverage_provisional.py
python run_match_assignments.py

echo "Fin pipeline: $(date '+%F %T')"
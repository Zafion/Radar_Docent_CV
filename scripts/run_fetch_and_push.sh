#!/usr/bin/env bash
set -Eeuo pipefail

LOCK_FILE="/tmp/funkcionario-local-fetch.lock"
LOG_DIR="/opt/funkcionario-fetcher/logs"
LOG_FILE="$LOG_DIR/fetch-$(date +'%Y%m%d-%H%M%S').log"

mkdir -p "$LOG_DIR"

exec 9>"$LOCK_FILE"

if ! flock -n 9; then
  echo "Another local fetch is already running."
  exit 0
fi

{
  echo "============================================================"
  echo "LOCAL CEICE FETCH AND PUSH"
  echo "Started at: $(date -Is)"
  echo "Host: $(hostname)"
  echo "============================================================"

  cd /opt/funkcionario-fetcher/repo/Radar_Docent_CV

  source .venv/bin/activate

  echo
  echo "1) Fetch CEICE from local network"
  python scripts/fetch_ceice_remote.py \
    --source-key family_adjudicacion \
    --download-delay 0.75

  echo
  echo "2) Push current fetch to Hetzner"
  rsync -avz --delete \
    /opt/funkcionario-fetcher/out/current/ \
    funkcionario-prod:/srv/funkcionario/remote_fetch/incoming/current/

  echo
  echo "3) Trigger import pipeline on Hetzner"
  ssh funkcionario-prod "/srv/funkcionario/app/scripts/run_remote_fetch_import_pipeline.sh"

  echo
  echo "============================================================"
  echo "Finished at: $(date -Is)"
  echo "OK"
} 2>&1 | tee "$LOG_FILE"


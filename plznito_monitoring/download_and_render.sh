#!/usr/bin/env bash
# Cron entry point: download new Plznito tickets and re-render both maps.
# Details go to plznito_monitoring/plznito_monitoring.log; stderr (cron mail / deploy log) only gets problems.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-python3}"
DIR=plznito_monitoring
LOG="$DIR/plznito_monitoring.log"

run_step() {
    local name=$1
    shift
    local started=$SECONDS
    if "$@"; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') INFO job: $name finished in $((SECONDS - started)) s" >> "$LOG"
    else
        local rc=$?
        echo "$(date '+%Y-%m-%d %H:%M:%S') ERROR job: $name FAILED with exit code $rc (details in $LOG)" | tee -a "$LOG" >&2
        exit "$rc"
    fi
}

echo "$(date '+%Y-%m-%d %H:%M:%S') INFO job: start" >> "$LOG"
run_step "download" "$PYTHON" -m plznito_monitoring.run_db_update \
    --db_json "$DIR/plznito_all.json" --write-cyklo-json "$DIR/plznito_cyklo.json"
run_step "render cycling map" "$PYTHON" -m plznito_monitoring.run_map_render --popup_mode api \
    --file_in "$DIR/plznito_cyklo.json" --file_out "$DIR/templates/plznito_map.html"
run_step "render all-tickets map" "$PYTHON" -m plznito_monitoring.run_map_render --popup_mode api --cluster_style \
    --file_in "$DIR/plznito_all.json" --file_out "$DIR/templates/plznito_map_all.html"

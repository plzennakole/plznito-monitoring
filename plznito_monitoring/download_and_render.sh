#!/usr/bin/env bash
# Cron entry point: download new Plznito tickets, re-render both maps and back up the DB once a day.
# Details go to plznito_monitoring/plznito_monitoring.log; stderr (cron mail / deploy log) only gets problems.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-python3}"
DIR=plznito_monitoring
LOG="$DIR/plznito_monitoring.log"
DB="$DIR/plznito.db"

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

# One backup a day, one file per weekday: a rolling week of copies without any cleanup.
BACKUP_DIR="$DIR/backups"
BACKUP="$BACKUP_DIR/plznito-$(date +%u).db"
mkdir -p "$BACKUP_DIR"

echo "$(date '+%Y-%m-%d %H:%M:%S') INFO job: start" >> "$LOG"
run_step "download" "$PYTHON" -m plznito_monitoring.run_db_update --db "$DB"
run_step "render cycling map" "$PYTHON" -m plznito_monitoring.run_map_render --popup_mode api \
    --db "$DB" --cyklo --file_out "$DIR/templates/plznito_map.html"
run_step "render all-tickets map" "$PYTHON" -m plznito_monitoring.run_map_render --popup_mode api --cluster_style \
    --db "$DB" --file_out "$DIR/templates/plznito_map_all.html"
# Skip the backup if this weekday's file is less than 20 hours old, i.e. already written today.
if [ -z "$(find "$BACKUP" -mmin -1200 2>/dev/null)" ]; then
    run_step "backup" "$PYTHON" -m plznito_monitoring.run_db_update --db "$DB" --backup "$BACKUP"
fi

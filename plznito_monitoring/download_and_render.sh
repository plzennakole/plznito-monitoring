#!/usr/bin/env bash
# Cron entry point: download new Plznito tickets and re-render both maps.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PYTHON="${PYTHON:-python3}"
DIR=plznito_monitoring

# Download the data
"$PYTHON" -m plznito_monitoring.run_db_update --db_json "$DIR/plznito_all.json" --write-cyklo-json "$DIR/plznito_cyklo.json"

# Render the data
"$PYTHON" -m plznito_monitoring.run_map_render --file_in "$DIR/plznito_cyklo.json" --file_out "$DIR/templates/plznito_map.html"
"$PYTHON" -m plznito_monitoring.run_map_render --popup_mode full --cluster_style --file_in "$DIR/plznito_all.json" --file_out "$DIR/templates/plznito_map_all.html"

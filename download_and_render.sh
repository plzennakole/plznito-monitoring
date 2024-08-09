#!/usr/bin/env bash

set pipefail -e

LOGLEVEL=INFO

# Download the data
python run_db_update.py --filter_cyklo --db_json plznito_cyklo.json
python run_db_update.py --db_json plznito_all.json

# Render the data
python run_map_render.py --file_in plznito_cyklo.json --file_out app/templates/map.html
python run_map_render.py --file_in plznito_all.json --file_out app/templates/map_all.html


# Plznito monitoring

Flask-based monitoring server for Plzeň cycling data: civic ticket tracking from plzni.to, cycling counter statistics, and train delays.

## Run

```shell
python run_flask.py
# or with gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:application
```

Server configuration is in `config.json` (port, debug mode, WSGI backend: `flask` / `tornado` / `gevent`).

---

## Web endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Endpoint listing |
| GET | `/plznito/map-bike` | Plzeň cycling ticket map |
| GET | `/plznito/map-all` | Plzeň all-tickets map |
| GET | `/train_delays/` | Train delays JSON (cached) |
| GET | `/bikecounters` | Cycling counters SPA |
| GET | `/bikecounters/api/nav` | Navigation tree (ECO-counter + cameras) |
| GET | `/bikecounters/api/location/<loc_id>` | Location metadata + collectors |
| GET | `/bikecounters/api/daily/<loc_id>` | Daily totals (combined + per-collector) |
| GET | `/bikecounters/api/weather` | Weather data `{date: {t, p}}` |

---

## Modules

### 1. Plznito map (`plznito_monitoring/`)

Scrapes civic tickets from plzni.to and renders interactive Folium maps.

**Update ticket data:**
```shell
cd plznito_monitoring

# Update cycling tickets only
python run_db_update.py --db_json plznito_cyklo.json

# Update all tickets
python run_db_update.py --db_json plznito_all.json

# Update all tickets and derive cycling subset in one pass
python run_db_update.py --db_json plznito_all.json --write-cyklo-json plznito_cyklo.json
```

Optional crawl tuning:
```shell
python run_db_update.py --db_json plznito_cyklo.json \
    --anchor-id 49553 --id-window-back 500 --id-lookahead 300 --seed-data-dir data
```

Restore-only mode (no live scrape):
```shell
python run_db_update.py --restore --db_json plznito_cyklo.json
```

**Render maps:**
```shell
python run_map_render.py --file_in plznito_cyklo.json \
    --file_out templates/plznito_map.html

python run_map_render.py --cluster_style --popup_mode full \
    --file_in plznito_all.json \
    --file_out templates/plznito_map_all.html
```

`--popup_mode compact` (default) produces smaller output; `full` includes the complete ticket text.

**Full pipeline (download + render):**
```shell
cd plznito_monitoring
bash download_and_render.sh
```

**Cron setup** (every 6 hours):
```shell
crontab -e
# append:
0 */6 * * * cd /path/to/plznito-monitoring/plznito_monitoring && python run_db_update.py --db_json plznito_all.json --write-cyklo-json plznito_cyklo.json && python run_map_render.py --file_in plznito_cyklo.json --file_out templates/plznito_map.html && python run_map_render.py --cluster_style --popup_mode full --file_in plznito_all.json --file_out templates/plznito_map_all.html
```

---

### 2. Cycling counters (`bikecounters_web/`)

Downloads ECO-counter and camera data from [opendata.plzen.eu](https://opendata.plzen.eu) and weather from ČHMÚ into a local SQLite database (`bikecounters_web/cyklo.db`).

**Update data:**
```shell
cd bikecounters_web

# Update everything (counters + weather)
python ingest.py

# Skip weather fetch
python ingest.py --no-weather

# Only a specific source
python ingest.py --source eco      # ECO-counter only
python ingest.py --source cam      # cameras only
python ingest.py --source weather  # weather only

# Force re-download of cached ČHMÚ historical CSVs
python ingest.py --delete-cache
```

**Cron setup** (daily at 03:00):
```shell
0 3 * * * cd /path/to/plznito-monitoring/bikecounters_web && python ingest.py
```



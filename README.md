# Plznito monitoring

Flask-based monitoring server for Plzeň cycling data: civic ticket tracking from plzni.to, cycling counter statistics, and train delays.

## Run

```shell
python run_flask.py
# or with gunicorn
gunicorn -w 2 -b 0.0.0.0:5000 app:app
```

Server configuration is in `config.json` (port, debug mode, WSGI backend: `flask` / `tornado` / `gevent`).

---

## Web endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Endpoint listing |
| GET | `/plznito/map-bike` | Plzeň cycling ticket map |
| GET | `/plznito/map-all` | Plzeň all-tickets map |
| GET | `/plznito/api/docs` | Plznito JSON API docs (Swagger UI), see below |
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

All commands run from the repo root as modules (`python -m ...`). Defaults (log file, `data/` seed dir, snapshots) live in `plznito_monitoring/`, regardless of the working directory.

**Update ticket data:**
```shell
# Update cycling tickets only (default --db_json is plznito_monitoring/plznito_cyklo.json)
python -m plznito_monitoring.run_db_update

# Update all tickets
python -m plznito_monitoring.run_db_update --db_json plznito_monitoring/plznito_all.json

# Update all tickets and derive cycling subset in one pass
python -m plznito_monitoring.run_db_update --db_json plznito_monitoring/plznito_all.json \
    --write-cyklo-json plznito_monitoring/plznito_cyklo.json
```

Optional crawl tuning:
```shell
python -m plznito_monitoring.run_db_update --anchor-id 49553 --id-window-back 500 --id-lookahead 300
```

Restore-only mode (no live scrape):
```shell
python -m plznito_monitoring.run_db_update --restore
```

**Render maps:**
```shell
python -m plznito_monitoring.run_map_render --file_in plznito_monitoring/plznito_cyklo.json \
    --file_out plznito_monitoring/templates/plznito_map.html

python -m plznito_monitoring.run_map_render --cluster_style --popup_mode full \
    --file_in plznito_monitoring/plznito_all.json \
    --file_out plznito_monitoring/templates/plznito_map_all.html
```

`--popup_mode compact` (default) produces smaller output; `full` includes the complete ticket text.

**Full pipeline (download + render):**
```shell
plznito_monitoring/download_and_render.sh   # works from any directory; PYTHON=... to pick the interpreter
```

**Cron setup** (every 6 hours):
```shell
0 */6 * * * /path/to/plznito-monitoring/plznito_monitoring/download_and_render.sh
```

**JSON API** (`app/plznito_api.py`, docs at `/plznito/api/docs`, spec at `/plznito/api/openapi.json`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/plznito/api/issues` | Issues, newest first. Filters: `since`, `until` (YYYY-MM-DD), `status_id`, `category_id`, `cyklo`, `since_id`; paging: `limit` (≤1000), `offset` |
| GET | `/plznito/api/issues/new?days=7` | Issues from today and the previous N days (same filters/paging) |
| GET | `/plznito/api/issues/<id>` | Single issue |
| GET | `/plznito/api/meta` | Last update time, totals, statuses and categories with counts |

The API reads `plznito_all.json` (override with `PLZNITO_DB_JSON`) into memory and reloads it when the file's mtime changes, i.e. after each cron run. Responses carry `Last-Modified` and `Cache-Control: public, max-age=300`; clients sending `If-Modified-Since` get `304` until new data is downloaded. For incremental sync, store the highest seen `id` and ask for `?since_id=<id>`.

---

### 2. Cycling counters (`bikecounters_web/`)

Downloads ECO-counter and camera data from [opendata.plzen.eu](https://opendata.plzen.eu) and weather from ČHMÚ into a local SQLite database (`bikecounters_web/cyklo.db`).

**Update data** (from the repo root):
```shell
# Update everything (counters + weather)
python -m bikecounters_web.ingest

# Skip weather fetch
python -m bikecounters_web.ingest --no-weather

# Only a specific source
python -m bikecounters_web.ingest --source eco      # ECO-counter only
python -m bikecounters_web.ingest --source cam      # cameras only
python -m bikecounters_web.ingest --source weather  # weather only

# Force re-download of cached ČHMÚ historical CSVs
python -m bikecounters_web.ingest --delete-cache
```

**Cron setup** (daily at 03:00; the wrapper logs to `bikecounters_web/ingest.log`):
```shell
0 3 * * * /path/to/plznito-monitoring/bikecounters_web/run_update.sh
```



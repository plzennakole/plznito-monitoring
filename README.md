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

Scrapes civic tickets from plznito.cz and renders interactive Leaflet maps.

All commands run from the repo root as modules (`python -m ...`). Defaults (log file, `data/` seed dir, snapshots) live in `plznito_monitoring/`, regardless of the working directory.

**Storage:** all tickets live in one SQLite file, `plznito_monitoring/plznito.db` (stdlib `sqlite3`, nothing to install; works on PythonAnywhere). Table `tickets` keeps each raw record as JSON in `data`, plus `is_cyklo` for the cycling subset; table `runs` logs every update with its stats and `ok` flag. Each update writes only the changed tickets in one transaction, so a crash or kill mid-run leaves the data as it was. The DB uses the default rollback journal, not WAL, which is unreliable on network file systems.

**Update ticket data:**
```shell
python -m plznito_monitoring.run_db_update                 # default --db plznito_monitoring/plznito.db
```

Optional crawl tuning:
```shell
python -m plznito_monitoring.run_db_update --anchor-id 49553 --id-window-back 500 --id-lookahead 300
```

Restore-only mode (no live scrape; merges the `plznito_monitoring/data/` snapshots into the DB):
```shell
# Optionally bulk-download a range of tickets into data/<id>.json first
python -m plznito_monitoring.restore_all --start-id 49000 --end-id 49600

python -m plznito_monitoring.run_db_update --restore
```

Import, export and backup:
```shell
# Merge a JSON list of records into the DB, e.g. the old plznito_all.json (one-time migration)
python -m plznito_monitoring.run_db_update --import-json plznito_monitoring/plznito_all.json
# Write all tickets as a JSON list
python -m plznito_monitoring.run_db_update --export-json plznito_all.json
# Consistent copy, safe while the web app reads the DB
python -m plznito_monitoring.run_db_update --backup plznito_monitoring/backups/plznito.db
```

Useful queries:
```shell
sqlite3 plznito_monitoring/plznito.db "SELECT id, started_at, finished_at, ok, stats FROM runs ORDER BY id DESC LIMIT 5;"
sqlite3 plznito_monitoring/plznito.db "SELECT COUNT(*), SUM(is_cyklo), MAX(id) FROM tickets;"
sqlite3 plznito_monitoring/plznito.db "SELECT json_extract(data, '$.name') FROM tickets WHERE id = 49971;"
```

**Render maps:**
```shell
python -m plznito_monitoring.run_map_render --popup_mode api --cyklo \
    --file_out plznito_monitoring/templates/plznito_map.html

python -m plznito_monitoring.run_map_render --cluster_style --popup_mode api \
    --file_out plznito_monitoring/templates/plznito_map_all.html
```

`--popup_mode compact` (default) shows name, date and status; `full` embeds the complete ticket text and photo in the page; `api` (used by both maps) loads text and photo from `/plznito/api/issues/<id>` when a popup opens, which keeps the page about 2.5x smaller.

The page layout lives in `plznito_monitoring/map_template.html` (plain HTML/CSS/JS; `run_map_render` only fills in the data). Filters and an open ticket are kept in the URL hash, e.g. `/plznito/map-all#t=365d&s=1,2&id=49965`, so a view can be shared. Add `?embed=1` to hide the page header when embedding a map in an iframe.

**Full pipeline (download + render + daily backup):**
```shell
plznito_monitoring/download_and_render.sh   # works from any directory; PYTHON=... to pick the interpreter
```

The first run of each day also writes `plznito_monitoring/backups/plznito-<weekday 1-7>.db`, a rolling week of backups. To restore one, stop the cron job and copy it over `plznito.db`.

**Cron setup** (every 6 hours):
```shell
0 */6 * * * /path/to/plznito-monitoring/plznito_monitoring/download_and_render.sh
```

**Logs:** `plznito_monitoring/plznito_monitoring.log` has the details; stderr (cron mail, deploy log) only gets warnings and errors. Each run ends with one line like

```
INFO plznito_monitoring.run_db_update: Update OK in 12.4 s: ids 55286-55586 (anchor 55386 from db), 49 HTTP requests, 83 tickets scraped (5 new, 12 updated, 66 unchanged), 76 ids without a ticket; plznito_monitoring/plznito.db now has 48112 records, newest #55391 from 20.10.2026.
```

and the wrapper adds `job: <step> finished in N s` or `job: <step> FAILED with exit code N`. The same run is in the `runs` table; a failed run has `ok = 0`, an interrupted one no `finished_at`. A run that finds no new ticket while the newest one is more than 3 days old logs a warning. If the DB has fallen behind (e.g. the job did not run for a while), the next run catches up to the newest ticket listed on plznito.cz (at most 20 000 ids per run) and says so in the log.

**uWSGI:** "OSError: write error" / "uwsgi_response_write_body_do(): Broken pipe" means a browser closed the connection before the response was sent; it cannot be retried server-side. Map pages are served gzipped (16 MB -> ~2.4 MB) with `ETag`/`Last-Modified`, so this is now rare. To keep such disconnects out of the error log, add to the uWSGI config:

```ini
ignore-sigpipe = true
ignore-write-errors = true
disable-write-exception = true
```

**JSON API** (`app/plznito_api.py`, docs at `/plznito/api/docs`, spec at `/plznito/api/openapi.json`):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/plznito/api/issues` | Issues, newest first. Filters: `since`, `until` (YYYY-MM-DD), `status_id`, `category_id`, `cyklo`, `since_id`; paging: `limit` (≤1000), `offset` |
| GET | `/plznito/api/issues/new?days=7` | Issues from today and the previous N days (same filters/paging) |
| GET | `/plznito/api/issues/<id>` | Single issue |
| GET | `/plznito/api/meta` | Last update time, totals, statuses and categories with counts |

The API reads `plznito.db` (override with `PLZNITO_DB`) into memory and reloads it after each successful update run. Responses carry `Last-Modified` and `Cache-Control: public, max-age=300`; clients sending `If-Modified-Since` get `304` until new data is downloaded. For incremental sync, store the highest seen `id` and ask for `?since_id=<id>`.

plznito.cz no longer publishes creation dates: scraped tickets only carry the date of their last status change. For those, `created` is estimated as the earliest date among the ticket and the next 50 ids (ids are assigned in creation order), marked `created_is_estimate: true`; on 42k tickets with known dates the estimate is within 1 day (median) and 4 days (p99). The maps use the same estimate for the time filters and show it as `≈ dd.mm.yyyy`.

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



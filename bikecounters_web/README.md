# Plzeň Cyklo — web app

Multi-location cycling counter dashboard. Combines eco-counter (induction loops)
and camera-based detectors from the Plzeň open data portal, with ČHMÚ weather overlay.

## Files

| File | Purpose |
|---|---|
| `config.py` | Location definitions, collector mappings, colors |
| `ingest.py` | Downloads all data sources → SQLite (`cyklo.db`) |
| `app.py` | Flask web server + JSON API |
| `templates/index.html` | Single-page frontend (Chart.js) |
| `run_update.sh` | Cron-friendly ingest wrapper |

## Setup

```bash
pip install flask requests
```

## First run

```bash
# Download all data and build the database
python3 ingest.py

# Start the web server (dev)
python3 app.py --debug
# → http://127.0.0.1:5000
```

## Production (gunicorn + nginx)

```bash
pip install gunicorn
gunicorn -w 2 -b 127.0.0.1:5000 app:app
```

Nginx config:
```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

## Cron setup

```cron
# Daily at 04:15 (data published at 04:00)
15 4 * * * /path/to/run_update.sh >> /path/to/ingest.log 2>&1
```

## Ingest options

```bash
python3 ingest.py                   # all sources
python3 ingest.py --source eco      # eco-counter only
python3 ingest.py --source cam      # cameras only
python3 ingest.py --source weather  # weather only
python3 ingest.py --no-weather      # skip ČHMÚ
python3 ingest.py --delete-cache    # force re-download of ČHMÚ historical CSVs
```

## Loading historical data

Historical camera/eco data can be inserted directly into SQLite:

```python
import sqlite3, config as cfg
db = sqlite3.connect(cfg.DB_PATH)

# Eco-counter historical example:
# source_id format: eco_{site_id}_in  /  eco_{site_id}_out
db.execute("INSERT OR REPLACE INTO counts VALUES (?,?,?,?)",
           ("eco_300048586_in", "2024-06-01 08:15:00", 12, 0))

# Camera historical example:
# source_id format: cam_{cam_id}_c{collector_id}
db.execute("INSERT OR REPLACE INTO counts VALUES (?,?,?,?)",
           ("cam_29_c8", "2024-06-01 08:15:00", 5, 0))
db.commit()
```

## API endpoints

| Endpoint | Response |
|---|---|
| `GET /api/nav` | Navigation tree for sidebar |
| `GET /api/location/<loc_id>` | Location metadata + collector list |
| `GET /api/daily/<loc_id>` | All daily totals (combined + per-collector) |
| `GET /api/weather` | All weather days `{date: {t, p}}` |

## Troubleshooting

| Symptom | Fix |
|---|---|
| Camera CSV columns not recognised | Check actual delimiter/header with `ingest.py --source cam` log |
| Weather gap (Jan–Mar) | Run `ingest.py --delete-cache` to refetch ČHMÚ |
| Missing Malesice directions | Collector IDs auto-discovered on first ingest; check log |

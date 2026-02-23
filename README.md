# Plznito monitoring

Simple monitoring of cycling tickets in Plzni.to and rendering to a Flask web map.

## Installation

Run on Flask (or Flask-compatible) server.

Requirements
```
Python >= 3.7
python-pip
git
```

Then clone this repository and install requirements:
```shell
git clone git@github.com:plzennakole/plznito-monitoring.git
cd plznito-monitoring
pip install -r requirements.txt
```

Update config in `config.json`.

For updating cycling data from plzni.to (recommended default), run every 6-24h:
```shell
python run_db_update.py --db_json plznito_cyklo.json
```

For updating all tickets:
```shell
python run_db_update.py --db_json plznito_all.json
```

For restore-only mode (does not run live update in the same invocation):
```shell
python run_db_update.py --restore --db_json plznito_cyklo.json
```

Render maps after data updates:
```shell
python run_map_render.py --file_in plznito_cyklo.json --file_out app/templates/map.html
python run_map_render.py --cluster_style --file_in plznito_all.json --file_out app/templates/map_all.html
```

Pipeline behavior notes:
- Update and render scripts skip malformed records and log summary counts instead of crashing.
- Map renderer accepts both `created.date` and fallback `date` fields in ticket JSON.

For adding to cron, open crontab:
```shell
crontab -e
```

Append entries for updates every midnight or every 6h:
```shell
0 0 * * * /path/to/python /path/to/plznito-monitoring/run_db_update.py --db_json /path/to/plznito-monitoring/plznito_cyklo.json
0 */6 * * * /path/to/python /path/to/plznito-monitoring/run_db_update.py --db_json /path/to/plznito-monitoring/plznito_cyklo.json
```

## Run
```shell
python run_flask.py --configs config.json
```

(c) Plzen na kole 2021

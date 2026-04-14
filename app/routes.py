import os
import sqlite3
import sys

from dotenv import load_dotenv
import pathlib

from flask import jsonify, request, render_template, abort, Response
from jinja2 import TemplateNotFound
from flask_caching import Cache

_BW_DIR = pathlib.Path(__file__).parent.parent / "bikecounters_web"
_PLZNITO_DIR = pathlib.Path(__file__).parent.parent / "plznito_monitoring"
sys.path.insert(0, str(_BW_DIR))
import config as bw_cfg

from app import app
from app.train_delays import scrape_babitron_delays

load_dotenv()


def _env_int(name, default):
    value = (os.getenv(name) or "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


TRAIN_DELAYS_SOURCE_R_URL = (
    os.getenv("TRAIN_DELAYS_SOURCE_R_URL") or "https://kam.mff.cuni.cz/~babilon/zponline"
)
TRAIN_DELAYS_SOURCE_OS_URL = (
    os.getenv("TRAIN_DELAYS_SOURCE_OS_URL") or "https://kam.mff.cuni.cz/~babilon/zponlineos"
)
CACHE_TIMEOUT_SECONDS = max(_env_int("TRAIN_DELAYS_CACHE_TIMEOUT_SECONDS", 60), 1)
CORS_ALLOW_ORIGIN = os.getenv("TRAIN_DELAYS_CORS_ALLOW_ORIGIN") or "*"
CORS_ALLOW_METHODS = os.getenv("TRAIN_DELAYS_CORS_ALLOW_METHODS") or "GET, OPTIONS"
CORS_ALLOW_HEADERS = os.getenv("TRAIN_DELAYS_CORS_ALLOW_HEADERS") or "Content-Type"
CORS_MAX_AGE = os.getenv("TRAIN_DELAYS_CORS_MAX_AGE") or "600"

cache = Cache(app, config={"CACHE_TYPE": "simple", "CACHE_DEFAULT_TIMEOUT": CACHE_TIMEOUT_SECONDS})


def query(sql, params=()):
    db = sqlite3.connect(bw_cfg.DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = CORS_MAX_AGE
    response.headers["Vary"] = "Origin"
    return response

@app.route('/')
def root():
    endpoints = [
        ("GET", "/train_delays/",              "Train delays (cached)"),
        ("GET", "/plznito/map-bike",            "Plzeň bike map"),
        ("GET", "/plznito/map-all",             "Plzeň full map"),
        ("GET", "/bikecounters",                "Cycling counters SPA"),
        ("GET", "/bikecounters/api/nav",                     "Navigation tree for cycling counters"),
        ("GET", "/bikecounters/api/location/<loc_id>",       "Location metadata"),
        ("GET", "/bikecounters/api/daily/<loc_id>",          "Daily totals (combined + per-collector)"),
        ("GET", "/bikecounters/api/weather",                 "Weather data {date: {t, p}}"),
    ]
    lines = ["<pre>"]
    for method, path, desc in endpoints:
        lines.append(f"{method:6}  {path:35}  {desc}")
    lines.append("</pre>")
    return Response("\n".join(lines), mimetype="text/html")

@app.route('/plznito/map-bike')
def index():
    try:
        return render_template('plznito_index.html')
    except TemplateNotFound:
        return render_template('map_missing.html',
            map_name='plznito_map.html',
            generate_command='cd plznito_monitoring && python run_map_render.py'
                             ' --file_in plznito_cyklo.json'
                             ' --file_out templates/plznito_map.html'), 503

@app.route('/plznito/map-all')
def plznito_map_all():
    try:
        return render_template('plznito_full_map_index.html')
    except TemplateNotFound:
        return render_template('map_missing.html',
            map_name='plznito_map_all.html',
            generate_command='cd plznito_monitoring && python run_map_render.py'
                             ' --cluster_style --popup_mode full'
                             ' --file_in plznito_all.json'
                             ' --file_out templates/plznito_map_all.html'), 503

@app.route('/train_delays/', methods=['GET', 'OPTIONS'])
@cache.cached()
def get_delays():
    if request.method == "OPTIONS":
        return ("", 204)
    delays_r = scrape_babitron_delays(TRAIN_DELAYS_SOURCE_R_URL)
    delays_os = scrape_babitron_delays(TRAIN_DELAYS_SOURCE_OS_URL)
    delays = {**delays_r, **delays_os}
    return jsonify(delays)


@app.route('/bikecounters')
def get_bikecounters():
    html_path = _BW_DIR / "templates" / "index.html"
    return Response(html_path.read_text(encoding="utf-8"), mimetype="text/html")


@app.route("/bikecounters/api/nav")
def api_nav():
    sections = {}
    for loc in bw_cfg.LOCATIONS:
        sec = loc["section"]
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(loc)

    result = []

    eco_items = []
    for loc in sections.get("eco", []):
        eco_items.append({
            "id":    loc["id"],
            "name":  loc["name"],
            "icon":  loc.get("icon", ""),
            "color": loc["color"],
        })
    result.append({"label": "ECO-COUNTER", "items": eco_items})

    cam_items = []
    seen_groups = set()
    for loc in sections.get("camera", []):
        gid = loc.get("group")
        if gid:
            if gid not in seen_groups:
                seen_groups.add(gid)
                group_info = bw_cfg.GROUPS.get(gid, {"label": gid})
                children = [
                    {
                        "id":    l["id"],
                        "name":  l["name"],
                        "color": l["color"],
                    }
                    for l in bw_cfg.LOCATIONS
                    if l.get("group") == gid
                ]
                cam_items.append({
                    "type":     "group",
                    "id":       gid,
                    "name":     group_info["label"],
                    "icon":     group_info.get("icon", ""),
                    "children": children,
                })
        else:
            cam_items.append({
                "type":  "item",
                "id":    loc["id"],
                "name":  loc["name"],
                "icon":  loc.get("icon", ""),
                "color": loc["color"],
            })

    result.append({"label": "KAMERY", "items": cam_items})
    return jsonify(result)


@app.route("/bikecounters/api/location/<loc_id>")
def api_location(loc_id):
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
    return jsonify({
        "id":         loc["id"],
        "name":       loc["name"],
        "color":      loc["color"],
        "type":       loc["type"],
        "collectors": loc["collectors"],
    })


@app.route("/bikecounters/api/daily/<loc_id>")
def api_daily(loc_id):
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)

    collectors = loc["collectors"]
    if not collectors:
        return jsonify({"combined": [], "collectors": []})

    source_ids = [c["source_id"] for c in collectors]
    placeholders = ",".join("?" * len(source_ids))

    combined_rows = query(
        f"""
        SELECT substr(ts, 1, 10) AS date,
               SUM(bikes)        AS bikes,
               SUM(scooters)     AS scooters
        FROM counts
        WHERE source_id IN ({placeholders})
        GROUP BY date
        ORDER BY date
        """,
        source_ids,
    )

    per_col_rows = query(
        f"""
        SELECT source_id,
               substr(ts, 1, 10) AS date,
               SUM(bikes)        AS bikes,
               SUM(scooters)     AS scooters
        FROM counts
        WHERE source_id IN ({placeholders})
        GROUP BY source_id, date
        ORDER BY source_id, date
        """,
        source_ids,
    )

    per_col_grouped = {}
    for row in per_col_rows:
        sid = row["source_id"]
        per_col_grouped.setdefault(sid, []).append({
            "date":     row["date"],
            "bikes":    row["bikes"],
            "scooters": row["scooters"],
        })

    collectors_out = []
    for c in collectors:
        sid = c["source_id"]
        collectors_out.append({
            "source_id": sid,
            "label":     c["label"],
            "color":     c["color"],
            "data":      per_col_grouped.get(sid, []),
        })

    return jsonify({
        "combined":   combined_rows,
        "collectors": collectors_out,
    })


@app.route("/bikecounters/api/weather")
def api_weather():
    rows = query("SELECT date, t, p FROM weather ORDER BY date")
    result = {r["date"]: {"t": r["t"], "p": r["p"]} for r in rows}
    return jsonify(result)

application = app

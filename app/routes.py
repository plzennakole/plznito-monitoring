import importlib.util
import os
import sqlite3

from dotenv import load_dotenv
import pathlib

from flask import jsonify, request, render_template, abort, Response
from jinja2 import TemplateNotFound
from flask_caching import Cache

_BW_DIR = pathlib.Path(__file__).parent.parent / "bikecounters_web"

_spec = importlib.util.spec_from_file_location("bikecounters_web.config", _BW_DIR / "config.py")
bw_cfg = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bw_cfg)

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
    with sqlite3.connect(bw_cfg.DB_PATH) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute(sql, params).fetchall()
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
        ("GET", "/bikecounters/api/counts/<loc_id>?resolution=hourly&from=YYYY-MM-DD&to=YYYY-MM-DD", "Counts for a location with optional date range and hourly/daily resolution"),
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

# ── Nav API ────────────────────────────────────────────────────────────────────
 
@app.route("/bikecounters/api/nav")
def api_nav():
    """Return navigation tree for the sidebar."""
    sections = {}
    for loc in bw_cfg.LOCATIONS:
        sec = loc["section"]
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(loc)
 
    result = []
 
    # ECO-COUNTER section
    eco_items = []
    for loc in sections.get("eco", []):
        eco_items.append({
            "id":   loc["id"],
            "name": loc["name"],
            "icon": loc.get("icon", ""),
            "color": loc["color"],
        })
    result.append({"label": "ECO-COUNTER", "items": eco_items})
 
    # CAMERA section — ungrouped and grouped
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
 
# ── Location detail API ────────────────────────────────────────────────────────
 
@app.route("/bikecounters/api/location/<loc_id>")
def api_location(loc_id):
    """Return location metadata including collectors list."""
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
    return jsonify({
        "id":           loc["id"],
        "name":         loc["name"],
        "color":        loc["color"],
        "type":         loc["type"],
        "collectors":   loc["collectors"],
    })
 
# ── Counts API ────────────────────────────────────────────────────────────────
 
@app.route("/bikecounters/api/counts/<loc_id>")
def api_counts(loc_id):
    """
    Return aggregated counts for a location.
 
    Query params:
      resolution  'hourly' | 'daily' (default: daily)
      from        YYYY-MM-DD  (optional)
      to          YYYY-MM-DD  (optional)
 
    Response: {resolution, combined: [{ts, bikes, scooters}], collectors: [...]}
    The 'ts' field is:
      hourly → 'YYYY-MM-DD HH' (e.g. '2025-09-15 08')
      daily  → 'YYYY-MM-DD'
    """
    from flask import request
 
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
 
    collectors = loc["collectors"]
    if not collectors:
        return jsonify({"resolution": "daily", "combined": [], "collectors": []})
 
    resolution  = request.args.get("resolution", "daily")
    from_date   = request.args.get("from")
    to_date     = request.args.get("to")
 
    allowed_resolutions = {
        "hourly": "substr(ts, 1, 13)",  # 'YYYY-MM-DD HH'
        "daily": "substr(ts, 1, 10)",   # 'YYYY-MM-DD'
    }
    if resolution not in allowed_resolutions:
        abort(400, description="Invalid resolution. Supported values are 'hourly' and 'daily'.")
 
    # SQL truncation expression per resolution
    trunc = allowed_resolutions[resolution]
 
    source_ids   = [c["source_id"] for c in collectors]
    placeholders = ",".join("?" * len(source_ids))
 
    # Optional date filter clause
    date_clause = ""
    date_params = []
    if from_date:
        date_clause += " AND ts >= ?"
        date_params.append(from_date)
    if to_date:
        # Include the full to_date day
        date_clause += " AND ts < date(?, '+1 day')"
        date_params.append(to_date)
 
    sql_combined = f"""
        SELECT {trunc}       AS period,
               SUM(bikes)   AS bikes,
               SUM(scooters) AS scooters
        FROM counts
        WHERE source_id IN ({placeholders}) {date_clause}
        GROUP BY period
        ORDER BY period
        """
    combined_rows_raw = query(sql_combined, source_ids + date_params)
    # rename 'period' back to 'ts' for the frontend
    combined_rows = [{"ts": r["period"], "bikes": r["bikes"], "scooters": r["scooters"]} for r in combined_rows_raw]
    import logging as _l
    if combined_rows:
        _l.getLogger(__name__).debug(
            "api_counts %s/%s: %d buckets, sample: %s",
            loc_id, resolution, len(combined_rows), combined_rows[-1])
 
    per_col_rows = query(
        f"""
        SELECT source_id,
               {trunc}       AS period,
               SUM(bikes)   AS bikes,
               SUM(scooters) AS scooters
        FROM counts
        WHERE source_id IN ({placeholders}) {date_clause}
        GROUP BY source_id, period
        ORDER BY source_id, period
        """,
        source_ids + date_params,
    )
 
    per_col_grouped = {}
    for row in per_col_rows:
        sid = row["source_id"]
        per_col_grouped.setdefault(sid, []).append({
            "ts":       row["period"],
            "bikes":    row["bikes"],
            "scooters": row["scooters"],
        })
 
    collectors_out = []
    for col in collectors:
        sid = col["source_id"]
        collectors_out.append({
            "source_id": sid,
            "label":     col["label"],
            "color":     col["color"],
            "data":      per_col_grouped.get(sid, []),
        })
 
    return jsonify({
        "resolution": resolution,
        "combined":   combined_rows,
        "collectors": collectors_out,
    })
 
# /api/daily kept as alias — frontend still calls it for initial load
@app.route("/bikecounters/api/daily/<loc_id>")
def api_daily(loc_id):
    from flask import request
    # Reuse api_counts with resolution=daily and pass through any query params
    request.environ['QUERY_STRING'] = 'resolution=daily'
    return api_counts(loc_id)
 
# ── Weather API ────────────────────────────────────────────────────────────────
 
@app.route("/bikecounters/api/weather")
def api_weather():
    """Return all weather data as {date: {t, p}} JSON."""
    rows = query("SELECT date, t, p FROM weather ORDER BY date")
    result = {r["date"]: {"t": r["t"], "p": r["p"]} for r in rows}
    return jsonify(result)
 
# ── Debug endpoint ────────────────────────────────────────────────────────────
 
@app.route("/bikecounters/api/debug/<loc_id>")
def api_debug(loc_id):
    """Show raw DB stats for a location — helps diagnose ingestion issues."""
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
    source_ids   = [c["source_id"] for c in loc["collectors"]]
    placeholders = ",".join("?" * len(source_ids))
 
    stats = query(f"""
        SELECT source_id,
               COUNT(*)        AS intervals,
               MIN(ts)         AS first_ts,
               MAX(ts)         AS last_ts,
               SUM(bikes)      AS total_bikes,
               SUM(scooters)   AS total_scooters,
               MAX(bikes)      AS max_interval_bikes
        FROM counts
        WHERE source_id IN ({placeholders})
        GROUP BY source_id
    """, source_ids)
 
    sample = query(f"""
        SELECT source_id, ts, bikes, scooters
        FROM counts
        WHERE source_id IN ({placeholders})
        ORDER BY ts DESC LIMIT 10
    """, source_ids)
 
    return jsonify({"location": loc_id, "sources": stats, "latest_rows": sample})


application = app

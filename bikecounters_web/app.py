#!/usr/bin/env python3
"""
app.py — Plzeň cycling counters web app
Run:  python3 app.py
Prod: gunicorn -w 2 -b 0.0.0.0:5000 app:app
"""

import sqlite3
from datetime import date
from pathlib import Path

from flask import Flask, jsonify, abort, Response
import sys
sys.path.insert(0, str(Path(__file__).parent))
import config as cfg

BASE_DIR = Path(__file__).parent
app = Flask(__name__,
            template_folder=str(BASE_DIR / "templates"),
            static_folder=str(BASE_DIR / "static"))
app.config["JSON_ENSURE_ASCII"] = False

# ── DB helper ──────────────────────────────────────────────────────────────────

def query(sql, params=()):
    db = sqlite3.connect(cfg.DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(sql, params).fetchall()
    db.close()
    return [dict(r) for r in rows]

# ── Nav API ────────────────────────────────────────────────────────────────────

@app.route("/api/nav")
def api_nav():
    """Return navigation tree for the sidebar."""
    sections = {}
    for loc in cfg.LOCATIONS:
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
                group_info = cfg.GROUPS.get(gid, {"label": gid})
                children = [
                    {
                        "id":    l["id"],
                        "name":  l["name"],
                        "color": l["color"],
                    }
                    for l in cfg.LOCATIONS
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

@app.route("/api/location/<loc_id>")
def api_location(loc_id):
    """Return location metadata including collectors list."""
    loc = cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
    return jsonify({
        "id":           loc["id"],
        "name":         loc["name"],
        "color":        loc["color"],
        "type":         loc["type"],
        "collectors":   loc["collectors"],
    })

# ── Daily data API ─────────────────────────────────────────────────────────────

@app.route("/api/daily/<loc_id>")
def api_daily(loc_id):
    """
    Return all available daily totals for a location.
    Response includes both combined and per-collector breakdowns.
    """
    loc = cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)

    collectors = loc["collectors"]
    if not collectors:
        return jsonify({"combined": [], "collectors": []})

    source_ids = [c["source_id"] for c in collectors]
    placeholders = ",".join("?" * len(source_ids))

    # Combined daily totals (sum across all collectors)
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

    # Per-collector daily totals
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

    # Group per-collector rows by source_id and attach metadata
    col_map = {c["source_id"]: c for c in collectors}
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

# ── Weather API ────────────────────────────────────────────────────────────────

@app.route("/api/weather")
def api_weather():
    """Return all weather data as {date: {t, p}} JSON."""
    rows = query("SELECT date, t, p FROM weather ORDER BY date")
    result = {r["date"]: {"t": r["t"], "p": r["p"]} for r in rows}
    return jsonify(result)

# ── Serve SPA ──────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    path = BASE_DIR / "templates" / "index.html"
    return Response(path.read_text(encoding="utf-8"), mimetype="text/html")

# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()
    app.run(host=args.host, port=args.port, debug=args.debug)
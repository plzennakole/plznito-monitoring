#!/usr/bin/env python3
"""
ingest.py — Download all cycling counter data + weather into SQLite.

Usage:
    python3 ingest.py                   # update all sources
    python3 ingest.py --no-weather      # skip ČHMÚ fetch
    python3 ingest.py --delete-cache    # force re-download of ČHMÚ historical CSVs
    python3 ingest.py --source eco      # only eco-counter
    python3 ingest.py --source cam      # only cameras
"""

import argparse
import io
import json
import logging
import sqlite3
import sys
import time
from datetime import date as dt_date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Database ───────────────────────────────────────────────────────────────────

def get_db():
    db = sqlite3.connect(cfg.DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    return db

def init_db():
    db = get_db()
    db.executescript("""
        CREATE TABLE IF NOT EXISTS counts (
            source_id   TEXT NOT NULL,
            ts          TEXT NOT NULL,
            bikes       INTEGER NOT NULL DEFAULT 0,
            scooters    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (source_id, ts)
        );
        CREATE INDEX IF NOT EXISTS idx_counts_date
            ON counts(source_id, substr(ts, 1, 10));

        CREATE TABLE IF NOT EXISTS weather (
            date TEXT PRIMARY KEY,
            t    REAL,
            p    REAL
        );
    """)
    db.commit()
    db.close()
    log.info("DB initialised at %s", cfg.DB_PATH)

# ── HTTP helper ────────────────────────────────────────────────────────────────

def fetch(url, timeout=60, encoding=None) -> str:
    try:
        import requests
    except ImportError:
        log.error("pip install requests")
        sys.exit(1)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    if encoding:
        return r.content.decode(encoding)
    for enc in ("utf-8-sig", "utf-8", "cp1250"):
        try:
            return r.content.decode(enc)
        except UnicodeDecodeError:
            continue
    return r.content.decode("utf-8", errors="replace")

# ── Eco-counter ────────────────────────────────────────────────────────────────

def ingest_ecocounter():
    log.info("Eco-counter → %s", cfg.ECO_URL)
    text = fetch(cfg.ECO_URL)
    lines = text.strip().splitlines()
    if not lines:
        log.warning("Empty eco-counter response")
        return

    header = [h.strip().strip('"').lower() for h in lines[0].split(";")]
    def col(name):
        try: return header.index(name)
        except ValueError: return None

    i_site = col("siteid") or col("site_id")
    i_ts   = col("timestamp")
    i_bi   = col("bike_count_in")
    i_bo   = col("bike_count_out")
    i_si   = col("scooter_count_in")
    i_so   = col("scooter_count_out")

    if None in (i_site, i_ts, i_bi, i_bo):
        log.error("Eco-counter CSV missing expected columns. Header: %s", header)
        return

    rows_in, rows_out = [], []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < max(i_ts, i_bi, i_bo) + 1:
            continue
        site_id = parts[i_site].strip().strip('"')
        ts_raw  = parts[i_ts].strip().strip('"')
        # Normalise timestamp to YYYY-MM-DD HH:MM:SS
        ts = ts_raw.replace("T", " ").replace("Z", "")[:19]
        if not ts or not ts[0].isdigit():
            continue
        try:
            bi = int(float(parts[i_bi].strip() or 0))
            bo = int(float(parts[i_bo].strip() or 0))
            si = int(float(parts[i_si].strip() or 0)) if i_si is not None else 0
            so = int(float(parts[i_so].strip() or 0)) if i_so is not None else 0
        except (ValueError, IndexError):
            continue

        rows_in.append((f"eco_{site_id}_in",  ts, bi, si))
        rows_out.append((f"eco_{site_id}_out", ts, bo, so))

    db = get_db()
    db.executemany(
        "INSERT OR REPLACE INTO counts(source_id, ts, bikes, scooters) VALUES(?,?,?,?)",
        rows_in + rows_out,
    )
    db.commit()
    db.close()
    log.info("  Eco-counter: %d interval records upserted", len(rows_in) + len(rows_out))

# ── Camera ─────────────────────────────────────────────────────────────────────

def _parse_camera_csv(text: str, cam_id: str) -> list[tuple]:
    """Parse a camera CSV and return list of (source_id, ts, bikes, scooters)."""
    import csv as csv_mod

    # Auto-detect delimiter
    sample = text[:2000]
    try:
        dialect = csv_mod.Sniffer().sniff(sample, delimiters="\t,;")
        delim = dialect.delimiter
    except csv_mod.Error:
        delim = "\t"

    lines = text.strip().splitlines()
    if not lines:
        return []

    header = [h.strip().strip('"').lower() for h in lines[0].split(delim)]

    def col(*names):
        for name in names:
            for i, h in enumerate(header):
                if h == name:
                    return i
        return None

    i_coll  = col("id kolektoru", "collector_id")
    i_start = col("začátek intervalu", "start", "timestamp")
    i_bikes = col("jízdní kola", "bikes", "kola")
    i_scoot = col("koloběžky", "scooters", "kolobezky")

    if None in (i_coll, i_start, i_bikes):
        log.warning("  Camera %s: unrecognised columns %s", cam_id, header)
        return []

    rows = []
    for line in lines[1:]:
        parts = line.split(delim)
        if len(parts) < max(filter(None, [i_coll, i_start, i_bikes])) + 1:
            continue
        try:
            coll_id = parts[i_coll].strip().strip('"')
            ts_raw  = parts[i_start].strip().strip('"')
            bikes   = int(float(parts[i_bikes].strip() or 0))
            scoot   = int(float(parts[i_scoot].strip() or 0)) if i_scoot is not None else 0
        except (ValueError, IndexError):
            continue

        # Parse Czech date format: "30.6.2025 23:58" → "2025-06-30 23:58:00"
        try:
            dt = datetime.strptime(ts_raw, "%d.%m.%Y %H:%M")
            ts = dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                ts = ts_raw.replace("T", " ")[:19]
            except Exception:
                continue

        source_id = f"cam_{cam_id}_c{coll_id}"
        rows.append((source_id, ts, bikes, scoot))
    return rows

def ingest_camera(cam_id: str, name: str):
    url = cfg.CAMERA_URL.format(cam_id=cam_id)
    log.info("Camera %s (%s) → %s", cam_id, name, url)
    try:
        text = fetch(url)
    except Exception as e:
        log.warning("  Download failed: %s", e)
        return

    rows = _parse_camera_csv(text, cam_id)
    if not rows:
        log.warning("  Camera %s: no rows parsed", cam_id)
        return

    db = get_db()
    db.executemany(
        "INSERT OR REPLACE INTO counts(source_id, ts, bikes, scooters) VALUES(?,?,?,?)",
        rows,
    )
    db.commit()

    # Auto-discover collectors not yet in config
    found = sorted({r[0] for r in rows})
    log.info("  %d records, collectors: %s", len(rows), found)

    # Update Malesice collector list dynamically if empty
    loc = next((l for l in cfg.LOCATIONS if l.get("cam_id") == cam_id), None)
    if loc and not loc["collectors"]:
        for i, sid in enumerate(found):
            coll_id = sid.split("_c")[-1]
            loc["collectors"].append({
                "collector_id": coll_id,
                "source_id": sid,
                "label": f"Směr {coll_id}",
                "color": cfg.PALETTE[i % len(cfg.PALETTE)],
            })
        cfg.SOURCE_MAP.update({c["source_id"]: (loc["id"], c) for c in loc["collectors"]})

    db.close()

# ── Weather ────────────────────────────────────────────────────────────────────

def _next_month(d: dt_date) -> dt_date:
    if d.month == 12:
        return dt_date(d.year + 1, 1, 1)
    return dt_date(d.year, d.month + 1, 1)

def _chmi_parse_csv(text: str, var: str) -> dict:
    """Parse ČHMÚ historical daily CSV. Returns {date_str: float|None}."""
    result = {}
    lines = text.splitlines()
    if not lines:
        return result
    first = lines[0]
    delim = "," if "," in first else ";"
    header = [h.strip().strip('"').lower() for h in first.split(delim)]

    def idx(*names):
        for name in names:
            for i, h in enumerate(header):
                if h == name: return i
        return None

    dt_col  = idx("dt", "date", "datum")
    val_col = idx("value", var.lower(), "hodnota")
    yr_col  = idx("rok", "year")
    mo_col  = idx("mesic", "month")
    dy_col  = idx("den", "day")

    use_dt  = dt_col is not None and val_col is not None
    use_ymd = all(x is not None for x in (yr_col, mo_col, dy_col, val_col))

    if not use_dt and not use_ymd:
        log.warning("ČHMÚ CSV %s: can't find date/value cols. Header: %s", var, header)
        return result

    for line in lines[1:]:
        if not line.strip(): continue
        parts = line.split(delim)
        try:
            if use_dt:
                date_str = str(parts[dt_col])[:10]
                if len(date_str) < 10 or not date_str[:4].isdigit(): continue
            else:
                date_str = f"{int(parts[yr_col]):04d}-{int(parts[mo_col]):02d}-{int(parts[dy_col]):02d}"
            raw = parts[val_col].strip().strip('"')
            val = None if raw in ("", "-999", "-999.9", "NA") else float(raw)
            result[date_str] = val
        except (ValueError, IndexError):
            continue
    return result

def _chmi_parse_recent_json(data) -> dict:
    """Parse ČHMÚ recent daily JSON. Returns {date: {"t": float|None, "p": float|None}}."""
    result = {}
    try:
        inner  = data["data"]["data"]
        header = [h.strip() for h in inner["header"].split(",")]
        values = inner["values"]
    except (KeyError, TypeError):
        return result

    def idx(name):
        try: return header.index(name)
        except ValueError: return None

    i_el, i_vt, i_dt, i_val = idx("ELEMENT"), idx("VTYPE"), idx("DT"), idx("VAL")
    if None in (i_el, i_vt, i_dt, i_val): return result

    for row in values:
        try:
            element, vtype, raw_dt, raw_val = str(row[i_el]), str(row[i_vt]), str(row[i_dt]), row[i_val]
        except (IndexError, TypeError): continue

        if element == "T" and vtype == "AVG":
            var_key = "t"
        elif element == "SRA":
            var_key = "p"
        else:
            continue

        date_str = raw_dt[:10]
        try:
            v = float(raw_val)
            val = None if v <= -999 else v
        except (ValueError, TypeError):
            val = None

        result.setdefault(date_str, {}).setdefault(var_key, val)
    return result

def ingest_weather(delete_cache=False):
    try:
        import requests
    except ImportError:
        log.warning("requests not available, skipping weather")
        return

    cfg.CHMI_CACHE_DIR.mkdir(exist_ok=True)
    if delete_cache:
        for f in cfg.CHMI_CACHE_DIR.glob("*.csv"):
            f.unlink()
            log.info("Deleted cache: %s", f)

    weather = {}  # {date: {"t": v, "p": v}}

    # ── 1. Historical CSVs (cached) ──────────────────────────────────────────
    CACHE_MAX_AGE = 30 * 86400
    for var, key, subdir in [("T", "t", "temperature"), ("SRA", "p", "precipitation")]:
        url        = f"{cfg.CHMI_HIST_BASE}/{subdir}/dly-0-20000-0-{cfg.CHMI_STATION}-{var}.csv"
        cache_file = cfg.CHMI_CACHE_DIR / f"{cfg.CHMI_STATION}-{var}.csv"
        use_cache  = cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_MAX_AGE

        if use_cache:
            log.info("ČHMÚ %s → cache (%dd old)", var, int((time.time()-cache_file.stat().st_mtime)/86400))
            text = cache_file.read_text(encoding="utf-8")
        else:
            log.info("ČHMÚ %s → %s", var, url)
            try:
                import requests as req
                r = req.get(url, timeout=120)
                r.raise_for_status()
                text = r.content.decode("utf-8-sig")
                cache_file.write_text(text, encoding="utf-8")
            except Exception as exc:
                log.warning("  %s download failed: %s", var, exc)
                text = cache_file.read_text(encoding="utf-8") if cache_file.exists() else ""

        parsed = _chmi_parse_csv(text, var)
        for d, v in parsed.items():
            weather.setdefault(d, {})[key] = v
        log.info("  %s: %d total days", var, len(parsed))

    # ── 2. Recent monthly JSON ────────────────────────────────────────────────
    import requests as req
    today = dt_date.today()
    # Fetch from 2 months back to catch gap between historical and now
    start_dt = dt_date(today.year, today.month, 1)
    if today.month > 2:
        start_dt = dt_date(today.year, today.month - 2, 1)
    else:
        start_dt = dt_date(today.year - 1, today.month + 10, 1)

    cur = start_dt
    while cur <= today:
        ym, mm = cur.strftime("%Y%m"), cur.strftime("%m")
        candidates = [
            f"{cfg.CHMI_RECENT_BASE}/dly-0-20000-0-{cfg.CHMI_STATION}-{ym}.json",
            f"{cfg.CHMI_RECENT_BASE}/{mm}/dly-0-20000-0-{cfg.CHMI_STATION}-{ym}.json",
        ]
        for url in candidates:
            try:
                r = req.get(url, timeout=30)
                if r.status_code == 404: continue
                r.raise_for_status()
                parsed = _chmi_parse_recent_json(r.json())
                n = 0
                for d, vd in parsed.items():
                    for k, v in vd.items():
                        weather.setdefault(d, {}).setdefault(k, v)
                    n += 1
                if n: log.info("  recent %s: %d days", ym, n)
                break
            except Exception as exc:
                log.debug("  recent %s (%s): %s", ym, url.split("/")[-2], exc)
        cur = _next_month(cur)

    # ── 3. Upsert into DB ────────────────────────────────────────────────────
    rows = []
    for d, v in weather.items():
        v.setdefault("t", None)
        v.setdefault("p", None)
        rows.append((d, v["t"], v["p"]))

    db = get_db()
    db.executemany("INSERT OR REPLACE INTO weather(date, t, p) VALUES(?,?,?)", rows)
    db.commit()
    db.close()
    log.info("Weather: %d days upserted", len(rows))

# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-weather",   action="store_true")
    parser.add_argument("--delete-cache", action="store_true")
    parser.add_argument("--source", choices=["eco", "cam", "weather", "all"], default="all")
    args = parser.parse_args()

    init_db()

    if args.source in ("all", "eco"):
        ingest_ecocounter()

    if args.source in ("all", "cam"):
        seen_cam_ids = set()
        for loc in cfg.LOCATIONS:
            if loc["type"] == "camera":
                cam_id = loc["cam_id"]
                if cam_id not in seen_cam_ids:
                    ingest_camera(cam_id, loc["name"])
                    seen_cam_ids.add(cam_id)

    if args.source in ("all", "weather") and not args.no_weather:
        ingest_weather(delete_cache=args.delete_cache)

    log.info("Done ✓")

if __name__ == "__main__":
    main()

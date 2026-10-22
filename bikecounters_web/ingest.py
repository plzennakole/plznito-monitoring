#!/usr/bin/env python3
"""
ingest.py — Download all cycling counter data + weather into SQLite.

Usage (from the repo root):
    python3 -m bikecounters_web.ingest                   # update all sources
    python3 -m bikecounters_web.ingest --no-weather      # skip ČHMÚ fetch
    python3 -m bikecounters_web.ingest --delete-cache    # force re-download of ČHMÚ historical CSVs
    python3 -m bikecounters_web.ingest --source eco      # only eco-counter
    python3 -m bikecounters_web.ingest --source cam      # only cameras
"""

import argparse
import logging
import shutil
import sqlite3
import sys
import time
from datetime import date as dt_date
from datetime import datetime
from pathlib import Path

from bikecounters_web import config as cfg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


class NotCsvError(Exception):
    """The server answered with something other than a delimited CSV table."""


# ── Database ───────────────────────────────────────────────────────────────────


def get_db():
    db = sqlite3.connect(cfg.DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA busy_timeout=5000")
    return db


def checkpoint_db():
    db = get_db()
    db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    db.close()


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


def _check_csv(text: str, url: str) -> None:
    """
    Make sure a downloaded body looks like a CSV table.

    The opendata.plzen.eu proxy answers every path with HTTP 200 and the body
    ``Request OK`` when the backend is down, so the status code alone is not enough.

    :param text: Decoded response body.
    :param url: URL the body came from, used in the error message.
    :raises NotCsvError: If the first line contains no ``;``, tab or ``,`` delimiter.
    """
    first_line = text.strip().partition("\n")[0]
    if not any(d in first_line for d in (';', '\t', ',')):
        raise NotCsvError(f"{url} returned non-CSV body: {text.strip()[:100]!r}")


def fetch(url, timeout=60, encoding=None) -> str:
    try:
        import requests
    except ImportError:
        log.error("pip install requests")
        sys.exit(1)
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    if encoding:
        text = r.content.decode(encoding)
    else:
        for enc in ("utf-8-sig", "utf-8", "cp1250"):
            try:
                text = r.content.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            text = r.content.decode("utf-8", errors="replace")
    if text.strip():
        _check_csv(text, url)
    return text


# ── Eco-counter ────────────────────────────────────────────────────────────────


def parse_ecocounter_csv(text: str) -> tuple[list[tuple], list[tuple]] | None:
    """
    Parse the eco-counter CSV export.

    :param text: Semicolon-separated CSV with a header row.
    :return: ``(rows_in, rows_out)`` of ``(source_id, ts, bikes, scooters)``, or ``None`` if the
        header lacks the expected columns.
    """
    lines = text.strip().splitlines()
    if not lines:
        return [], []

    header = [h.strip().strip('"').lower() for h in lines[0].split(";")]

    def col(name: str) -> int | None:
        try:
            return header.index(name)
        except ValueError:
            return None

    i_site = col("siteid")
    if i_site is None:
        i_site = col("site_id")
    i_ts = col("timestamp")
    i_bi = col("bike_count_in")
    i_bo = col("bike_count_out")
    i_si = col("scooter_count_in")
    i_so = col("scooter_count_out")

    if i_site is None or i_ts is None or i_bi is None or i_bo is None:
        log.error("Eco-counter CSV missing expected columns. Header: %s", header)
        return None

    rows_in, rows_out = [], []
    for line in lines[1:]:
        parts = line.split(";")
        if len(parts) < max(i_site, i_ts, i_bi, i_bo) + 1:
            continue
        site_id = parts[i_site].strip().strip('"')
        ts_raw = parts[i_ts].strip().strip('"')
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

        rows_in.append((f"eco_{site_id}_in", ts, bi, si))
        rows_out.append((f"eco_{site_id}_out", ts, bo, so))
    return rows_in, rows_out


def ingest_ecocounter() -> int:
    """
    Download the eco-counter CSV and upsert it into the DB.

    :return: Number of interval records upserted.
    """
    log.info("Eco-counter → %s", cfg.ECO_URL)
    try:
        text = fetch(cfg.ECO_URL)
    except NotCsvError as e:
        log.error("  %s", e)
        return 0
    if not text.strip():
        log.warning("Empty eco-counter response")
        return 0

    parsed = parse_ecocounter_csv(text)
    if parsed is None:
        return 0
    rows_in, rows_out = parsed

    db = get_db()
    try:
        db.executemany(
            "INSERT OR REPLACE INTO counts(source_id, ts, bikes, scooters) VALUES(?,?,?,?)",
            rows_in + rows_out,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        log.error("Eco-counter DB write failed: %s", e)
        raise
    finally:
        db.close()
    if rows_in:
        log.info("  Eco-counter: %d interval records upserted", len(rows_in) + len(rows_out))
        log.info("  Sample (last 3 rows_in): %s", rows_in[-3:])
        log.info("  Sample (last 3 rows_out): %s", rows_out[-3:])
    else:
        log.warning("  Eco-counter: 0 rows parsed — check CSV format")
    return len(rows_in) + len(rows_out)


# ── Camera ─────────────────────────────────────────────────────────────────────


def _parse_camera_csv(text: str, cam_id: str) -> list[tuple]:
    """Parse a camera CSV and return list of (source_id, ts, bikes, scooters)."""
    lines = text.strip().splitlines()
    if not lines:
        return []

    # The API returns tab-separated data lines. The PowerShell script may have
    # replaced the header with a semicolon version — detect delimiter from first DATA line.
    delim = '\t'
    if len(lines) > 1:
        sample = lines[1]
        if '\t' in sample:
            delim = '\t'
        elif ';' in sample:
            delim = ';'
        else:
            delim = ','

    # Header may use a different delimiter than data
    header_line = lines[0]
    header_delim = delim
    for hd in (';', '\t', ','):
        if len(header_line.split(hd)) >= 4:
            header_delim = hd
            break

    header = [h.strip().strip('"').lower() for h in header_line.split(header_delim)]
    log.debug("  Camera %s: header_delim=%r data_delim=%r cols=%s", cam_id, header_delim, delim, header)

    def col(*names):
        for name in names:
            for i, h in enumerate(header):
                if h == name:
                    return i
        return None

    i_coll = col("id kolektoru", "collector_id")
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
            ts_raw = parts[i_start].strip().strip('"')
            bikes = int(float(parts[i_bikes].strip() or 0))
            scoot = int(float(parts[i_scoot].strip() or 0)) if i_scoot is not None else 0
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


def ingest_camera(cam_id: str, name: str) -> int:
    """
    Download one camera's CSV and upsert it into the DB.

    :param cam_id: Camera ID used in the API URL.
    :param name: Human-readable location name, for logging.
    :return: Number of records upserted.
    """
    url = cfg.CAMERA_URL.format(cam_id=cam_id)
    log.info("Camera %s (%s) → %s", cam_id, name, url)
    try:
        text = fetch(url)
    except Exception as e:
        log.warning("  Download failed: %s", e)
        return 0

    rows = _parse_camera_csv(text, cam_id)
    if not rows:
        log.warning("  Camera %s: no rows parsed", cam_id)
        return 0

    db = get_db()
    try:
        db.executemany(
            "INSERT OR REPLACE INTO counts(source_id, ts, bikes, scooters) VALUES(?,?,?,?)",
            rows,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        log.error("Camera %s DB write failed: %s", cam_id, e)
        raise

    # Auto-discover collectors not yet in config
    found = sorted({r[0] for r in rows})
    log.info("  %d records, collectors: %s", len(rows), found)

    # Update Malesice collector list dynamically if empty
    loc = next((location for location in cfg.LOCATIONS if location.get("cam_id") == cam_id), None)
    if loc and not loc["collectors"]:
        for i, sid in enumerate(found):
            coll_id = sid.split("_c")[-1]
            loc["collectors"].append(
                {
                    "collector_id": coll_id,
                    "source_id": sid,
                    "label": f"Směr {coll_id}",
                    "color": cfg.PALETTE[i % len(cfg.PALETTE)],
                }
            )
        cfg.SOURCE_MAP.update({c["source_id"]: (loc["id"], c) for c in loc["collectors"]})

    db.close()
    return len(rows)


# ── Weather ────────────────────────────────────────────────────────────────────


def _next_month(d: dt_date) -> dt_date:
    if d.month == 12:
        return dt_date(d.year + 1, 1, 1)
    return dt_date(d.year, d.month + 1, 1)


def _chmi_parse_csv(text: str, var: str) -> dict:
    """Parse ČHMÚ historical daily CSV. Returns {date_str: float|None}."""
    result: dict[str, float | None] = {}
    lines = text.splitlines()
    if not lines:
        return result
    first = lines[0]
    delim = "," if "," in first else ";"
    header = [h.strip().strip('"').lower() for h in first.split(delim)]

    def idx(*names):
        for name in names:
            for i, h in enumerate(header):
                if h == name:
                    return i
        return None

    dt_col = idx("dt", "date", "datum")
    val_col = idx("value", var.lower(), "hodnota")
    yr_col = idx("rok", "year")
    mo_col = idx("mesic", "month")
    dy_col = idx("den", "day")

    use_dt = dt_col is not None and val_col is not None
    use_ymd = all(x is not None for x in (yr_col, mo_col, dy_col, val_col))

    if not use_dt and not use_ymd:
        log.warning("ČHMÚ CSV %s: can't find date/value cols. Header: %s", var, header)
        return result

    for line in lines[1:]:
        if not line.strip():
            continue
        parts = line.split(delim)
        try:
            if use_dt:
                date_str = str(parts[dt_col])[:10]
                if len(date_str) < 10 or not date_str[:4].isdigit():
                    continue
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
    result: dict[str, dict[str, float | None]] = {}
    try:
        inner = data["data"]["data"]
        header = [h.strip() for h in inner["header"].split(",")]
        values = inner["values"]
    except (KeyError, TypeError):
        return result

    def idx(name):
        try:
            return header.index(name)
        except ValueError:
            return None

    i_el, i_vt, i_dt, i_val = idx("ELEMENT"), idx("VTYPE"), idx("DT"), idx("VAL")
    if None in (i_el, i_vt, i_dt, i_val):
        return result

    for row in values:
        try:
            element, vtype, raw_dt, raw_val = str(row[i_el]), str(row[i_vt]), str(row[i_dt]), row[i_val]
        except (IndexError, TypeError):
            continue

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
        import requests  # noqa: F401 - availability check only
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
        url = f"{cfg.CHMI_HIST_BASE}/{subdir}/dly-0-20000-0-{cfg.CHMI_STATION}-{var}.csv"
        cache_file = cfg.CHMI_CACHE_DIR / f"{cfg.CHMI_STATION}-{var}.csv"
        use_cache = cache_file.exists() and (time.time() - cache_file.stat().st_mtime) < CACHE_MAX_AGE

        if use_cache:
            log.info("ČHMÚ %s → cache (%dd old)", var, int((time.time() - cache_file.stat().st_mtime) / 86400))
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
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                parsed = _chmi_parse_recent_json(r.json())
                n = 0
                for d, vd in parsed.items():
                    for k, v in vd.items():
                        weather.setdefault(d, {}).setdefault(k, v)
                    n += 1
                if n:
                    log.info("  recent %s: %d days", ym, n)
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
    try:
        db.executemany("INSERT OR REPLACE INTO weather(date, t, p) VALUES(?,?,?)", rows)
        db.commit()
    except Exception as e:
        db.rollback()
        db.close()
        log.error("Weather DB write failed: %s", e)
        raise
    db.close()
    log.info("Weather: %d days upserted", len(rows))


# ── Main ───────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-weather", action="store_true")
    parser.add_argument("--delete-cache", action="store_true")
    parser.add_argument("--source", choices=["eco", "cam", "weather", "all"], default="all")
    args = parser.parse_args()

    db_dir = str(Path(cfg.DB_PATH).parent)
    free_mb = shutil.disk_usage(db_dir).free / 1024**2
    if free_mb < 200:
        log.error("Low disk space: %.0f MB free — aborting ingest to protect DB integrity", free_mb)
        sys.exit(1)
    log.info("Disk free: %.0f MB", free_mb)

    init_db()

    bike_rows = 0
    if args.source in ("all", "eco"):
        bike_rows += ingest_ecocounter()

    if args.source in ("all", "cam"):
        seen_cam_ids = set()
        for loc in cfg.LOCATIONS:
            if loc["type"] == "camera":
                cam_id = loc["cam_id"]
                if cam_id not in seen_cam_ids:
                    bike_rows += ingest_camera(cam_id, loc["name"])
                    seen_cam_ids.add(cam_id)

    if args.source in ("all", "weather") and not args.no_weather:
        ingest_weather(delete_cache=args.delete_cache)

    checkpoint_db()
    if args.source in ("all", "eco", "cam") and bike_rows == 0:
        log.error("No bike-counter rows ingested from any source")
        sys.exit(1)
    log.info("Done ✓")


if __name__ == "__main__":
    main()

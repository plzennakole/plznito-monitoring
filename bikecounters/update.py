#!/usr/bin/env python3
"""
update.py — Plzeň Eco-counter widget updater
=============================================
Downloads the latest XLSX from the city open-data portal,
converts it to the expected CSV format, and rebuilds the
self-contained HTML widget.

Usage:
    python3 update.py                   # uses default paths
    python3 update.py --out /var/www/html/cyklo.html
    python3 update.py --xlsx local.xlsx # skip download, use local file

Requirements:
    pip install requests openpyxl
"""

import argparse
import csv
import io
import logging
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# ── Config ─────────────────────────────────────────────────────────────────────

XLSX_URL   = "https://opendata.plzen.eu/public/opendata/dataset/198"
XLSX_URL   = "https://opendata.plzen.eu/public/opendata/ecocounter-traffic"
SCRIPT_DIR = Path(__file__).parent
TEMPLATE   = SCRIPT_DIR / "template.html"
OUTPUT     = SCRIPT_DIR / "cyklo-counter.html"

# Expected CSV columns (semicolon-separated, as produced by the city)
EXPECTED_COLS = ["id", "siteId", "timestamp", "bike_count_in", "bike_count_out",
                 "scooter_count_in", "scooter_count_out"]

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Helpers ────────────────────────────────────────────────────────────────────

def download_xlsx(url: str) -> bytes:
    """Download the XLSX file from the city portal."""
    try:
        import requests
    except ImportError:
        log.error("'requests' not installed. Run: pip install requests")
        sys.exit(1)

    log.info("Downloading XLSX from %s", url)
    r = requests.get(url, timeout=60)
    r.raise_for_status()

    ct = r.headers.get("Content-Type", "")
    if "html" in ct:
        raise ValueError(
            f"Server returned HTML instead of XLSX (Content-Type: {ct}). "
            "The URL may have changed or requires authentication."
        )

    log.info("Downloaded %.1f KB", len(r.content) / 1024)
    return r.content


def xlsx_to_csv(xlsx_bytes: bytes) -> str:
    """
    Parse the XLSX and return a semicolon-separated CSV string
    matching the format of ecocounter_cidla.csv.

    The XLSX sheet is expected to have columns (case-insensitive):
        id, siteId, timestamp, bike_count_in, bike_count_out,
        scooter_count_in, scooter_count_out
    """
    try:
        import openpyxl
    except ImportError:
        log.error("'openpyxl' not installed. Run: pip install openpyxl")
        sys.exit(1)

    # if already csv return
    log.info("Checking if input is already CSV...")
    log.info("XLSX bytes: %s", xlsx_bytes[:100])
    if xlsx_bytes.startswith(b"\xef\xbb\xbfid"):
        return xlsx_bytes.decode("utf-8")

    wb = openpyxl.load_workbook(io.BytesIO(xlsx_bytes), read_only=True, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        raise ValueError("XLSX sheet is empty")

    # Normalise header: strip, lower, replace spaces/dashes with underscore
    raw_header = rows[0]
    header = [str(h).strip().lower().replace(" ", "_").replace("-", "_")
              if h is not None else "" for h in raw_header]

    log.info("XLSX columns: %s", header)

    # Map XLSX column names → our expected names (flexible matching)
    col_map = {}
    aliases = {
        "id":               ["id", "row_id"],
        "siteId":           ["siteid", "site_id"],
        "timestamp":        ["timestamp", "time", "datetime", "cas", "datum"],
        "bike_count_in":    ["bike_count_in",  "bikes_in",  "kola_in"],
        "bike_count_out":   ["bike_count_out", "bikes_out", "kola_out"],
        "scooter_count_in": ["scooter_count_in",  "scooters_in",  "kolobezky_in"],
        "scooter_count_out":["scooter_count_out", "scooters_out", "kolobezky_out"],
    }
    for target, candidates in aliases.items():
        for i, h in enumerate(header):
            if h in candidates:
                col_map[target] = i
                break

    missing = [c for c in EXPECTED_COLS if c not in col_map]
    if missing:
        raise ValueError(
            f"Could not find columns {missing} in XLSX. "
            f"Available: {header}"
        )

    log.info("Column mapping: %s", col_map)

    # Build CSV output
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(EXPECTED_COLS)

    n = 0
    for row in rows[1:]:
        def cell(name):
            v = row[col_map[name]]
            if v is None:
                return ""
            if isinstance(v, datetime):
                return v.strftime("%Y-%m-%d %H:%M:%S")
            return str(v)

        ts_raw = cell("timestamp")
        # If the timestamp has surrounding quotes already, strip them
        ts = ts_raw.strip('"')

        writer.writerow([
            cell("id"),
            cell("siteId"),
            f'"{ts}"',          # keep timestamp quoted, matching original format
            cell("bike_count_in"),
            cell("bike_count_out"),
            cell("scooter_count_in"),
            cell("scooter_count_out"),
        ])
        n += 1

    log.info("Converted %d data rows", n)
    return out.getvalue()


def embed_csv(csv_text: str, template_path: Path, output_path: Path) -> None:
    """Replace the {{CSV_DATA}} placeholder in the template and write output."""
    template = template_path.read_text(encoding="utf-8")
    if "{{CSV_DATA}}" not in template:
        raise ValueError(f"Placeholder {{{{CSV_DATA}}}} not found in {template_path}")

    # Escape characters that would break a JS template literal
    safe = csv_text.replace("\\", "\\\\").replace("`", "\\`").replace("${", "\\${")

    html = template.replace("{{CSV_DATA}}", safe)
    output_path.write_text(html, encoding="utf-8")
    log.info("Widget written to %s (%.1f KB)", output_path, len(html) / 1024)


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Rebuild the Plzeň cyklo-counter widget.")
    parser.add_argument("--url",      default=XLSX_URL,  help="XLSX download URL")
    parser.add_argument("--xlsx",     default=None,       help="Use a local XLSX file instead of downloading")
    parser.add_argument("--template", default=TEMPLATE,  type=Path, help="HTML template file")
    parser.add_argument("--out",      default=OUTPUT,    type=Path, help="Output HTML file")
    parser.add_argument("--csv-out",  default=None,      type=Path, help="Also save converted CSV here")
    args = parser.parse_args()

    # 1. Get raw XLSX bytes
    if args.xlsx:
        log.info("Using local XLSX file: %s", args.xlsx)
        xlsx_bytes = Path(args.xlsx).read_bytes()
    else:
        xlsx_bytes = download_xlsx(args.url)

    # 2. Convert to CSV
    csv_text = xlsx_to_csv(xlsx_bytes)

    # 3. Optionally save CSV
    if args.csv_out:
        args.csv_out.write_text(csv_text, encoding="utf-8")
        log.info("CSV saved to %s", args.csv_out)

    # 4. Embed into HTML template
    embed_csv(csv_text, args.template, args.out)

    log.info("Done ✓")


if __name__ == "__main__":
    main()

import os
import re
import unicodedata

import requests
from bs4 import BeautifulSoup

from dotenv import load_dotenv
from fake_headers import Headers
from flask import Flask, jsonify, request
from flask_caching import Cache

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

app = Flask(__name__)
cache = Cache(app, config={"CACHE_TYPE": "simple", "CACHE_DEFAULT_TIMEOUT": CACHE_TIMEOUT_SECONDS})


TRAIN_ID_RE = re.compile(r"\b([A-Za-z]{1,6})\s*([0-9]{1,6})\b")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = CORS_MAX_AGE
    response.headers["Vary"] = "Origin"
    return response


def get_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text().strip()


def normalize_text(text):
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def extract_inner_html(cell):
    return "".join(str(child) for child in cell.contents).strip()


def get_delay(text):
    if not text:
        return None
    text_norm = normalize_text(get_text(text))
    if "bez zpozdeni" in text_norm:
        return 0
    if "vcas" in text_norm:
        return 0
    if "zrusen" in text_norm:
        return None
    if "odklon" in text_norm:
        return None
    if "vyluka" in text_norm:
        return None
    for token in text_norm.split():
        cleaned = token.strip(".,;")
        match = re.fullmatch(r"\+?(\d{1,3})", cleaned)
        if match:
            return int(match.group(1))
    return None


def parse_delay_status_and_minutes(delay_text):
    text_norm = normalize_text(get_text(delay_text))
    if "bez zpozdeni" in text_norm or "vcas" in text_norm:
        return "on_time", 0
    if "zrusen" in text_norm:
        return "canceled", None
    if "odklon" in text_norm:
        return "diverted", None
    if "vyluka" in text_norm:
        return "disruption", None

    for token in text_norm.split():
        cleaned = token.strip(".,;")
        match = re.fullmatch(r"\+?(\d{1,3})", cleaned)
        if match:
            return "delayed", int(match.group(1))
    return "unknown", None


def parse_train_identity(train_text):
    match = TRAIN_ID_RE.search(train_text)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def parse_scheduled_actual_times(scheduled_actual_text):
    times = TIME_RE.findall(scheduled_actual_text)
    if not times:
        return None, None
    if len(times) == 1:
        return times[0], None
    return times[0], times[1]


def source_page_from_url(url):
    lowered = url.lower()
    if lowered.endswith("zponlineos"):
        return "zponlineos"
    return "zponline"


def scrape_babitron_delays(url):
    results = {}
    source_page = source_page_from_url(url)

    headers = Headers(headers=True).generate()
    response = requests.get(url, headers=headers, timeout=30)
    if response.status_code != 200:
        raise Exception(f"Chyba při stahování stránky: {response.status_code}")

    soup = BeautifulSoup(response.text, "html.parser")
    tables = soup.find_all("table", {"align": "CENTER", "bgcolor": "0000ff"})

    if not tables:
        print("Tabulka se zpožděními nebyla nalezena.")
        return results

    for table in tables:
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 6:
                continue

            train_info = get_text(str(cells[0]))
            if not train_info:
                continue
            train_name = extract_inner_html(cells[1])
            route = extract_inner_html(cells[2])
            station = extract_inner_html(cells[3])
            scheduled_actual_time = extract_inner_html(cells[4])
            delay_text = extract_inner_html(cells[5])
            delay_info = get_delay(delay_text)
            status, delay_minutes = parse_delay_status_and_minutes(delay_text)
            train_category, train_number = parse_train_identity(train_info)
            route_text = get_text(route)
            station_text = get_text(station)
            scheduled_text = get_text(scheduled_actual_time)
            scheduled_time_hhmm, actual_time_hhmm = parse_scheduled_actual_times(scheduled_text)

            results[train_info] = {
                    "train": train_info,
                    "name": train_name,
                    "route": route,
                    "station": station,
                    "scheduled_actual_time": scheduled_actual_time,
                    "delay_text": delay_text,
                    "delay": delay_info,
                    "status": status,
                    "delay_minutes": delay_minutes,
                    "train_category": train_category,
                    "train_number": train_number,
                    "route_text": route_text,
                    "station_text": station_text,
                    "scheduled_time_hhmm": scheduled_time_hhmm,
                    "actual_time_hhmm": actual_time_hhmm,
                    "source_page": source_page,
            }

    return results

@app.route('/train_delays/', methods=['GET', 'OPTIONS'])
@cache.cached()
def get_delays():
    if request.method == "OPTIONS":
        return ("", 204)
    delays_r = scrape_babitron_delays(TRAIN_DELAYS_SOURCE_R_URL)
    delays_os = scrape_babitron_delays(TRAIN_DELAYS_SOURCE_OS_URL)
    delays = {**delays_r, **delays_os}
    return jsonify(delays)

application = app


if __name__ == "__main__":
    app.run(debug=True)

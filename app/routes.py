import os

from dotenv import load_dotenv
from flask import jsonify, request
from flask_caching import Cache

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


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = CORS_MAX_AGE
    response.headers["Vary"] = "Origin"
    return response


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

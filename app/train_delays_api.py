# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Train delays endpoint (scraped from Babitron, cached)."""

import os
from concurrent.futures import ThreadPoolExecutor

from flask import Blueprint, Response, jsonify, request

from app.cors import add_cors_headers
from app.extensions import cache
from app.settings import env_int
from app.train_delays import scrape_babitron_delays

TRAIN_DELAYS_SOURCE_R_URL = os.getenv('TRAIN_DELAYS_SOURCE_R_URL') or 'https://babitron.kam.mff.cuni.cz/zponline.html'
TRAIN_DELAYS_SOURCE_OS_URL = (
    os.getenv('TRAIN_DELAYS_SOURCE_OS_URL') or 'https://babitron.kam.mff.cuni.cz/zponlineos.html'
)
CACHE_TIMEOUT_SECONDS = max(env_int('TRAIN_DELAYS_CACHE_TIMEOUT_SECONDS', 60), 1)

bp = Blueprint('train_delays', __name__)
bp.after_request(add_cors_headers)


@bp.route('/train_delays/', methods=['GET', 'OPTIONS'])
@cache.cached(timeout=CACHE_TIMEOUT_SECONDS, unless=lambda: request.method != 'GET')
def get_delays() -> Response | tuple[str, int]:
    """Train delays JSON (cached)."""
    if request.method == 'OPTIONS':
        return '', 204
    # Both pages in parallel; the OS page wins on duplicate trains.
    with ThreadPoolExecutor(max_workers=2) as executor:
        delays_r, delays_os = executor.map(
            scrape_babitron_delays, [TRAIN_DELAYS_SOURCE_R_URL, TRAIN_DELAYS_SOURCE_OS_URL]
        )
    return jsonify({**delays_r, **delays_os})

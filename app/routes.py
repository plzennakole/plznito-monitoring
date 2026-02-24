import logging
from pathlib import Path

from flask import jsonify, render_template
from flask_caching import Cache

from app.train_delays import scrape_babitron_delays
from app import app

logger = logging.getLogger(__name__)

# CORS Configuration
CORS_ALLOW_ORIGIN = "*"
CORS_ALLOW_METHODS = "GET, POST, OPTIONS"
CORS_ALLOW_HEADERS = "Content-Type"
CORS_MAX_AGE = "3600"

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = CORS_ALLOW_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = CORS_ALLOW_METHODS
    response.headers["Access-Control-Allow-Headers"] = CORS_ALLOW_HEADERS
    response.headers["Access-Control-Max-Age"] = CORS_MAX_AGE
    response.headers["Vary"] = "Origin"
    return response

cache = Cache(app, config={'CACHE_TYPE': 'simple'})


def _template_exists(template_name):
    return (Path(app.root_path) / "templates" / template_name).exists()


@app.route('/')
def index():
    if not _template_exists("map.html"):
        return render_template(
            "map_missing.html",
            map_name="map.html",
            generate_command="python run_map_render.py --file_in plznito_cyklo.json --file_out app/templates/map.html",
        )
    return render_template('index.html')


@app.route('/plznito_map_all')
def plznito_map_all():
    if not _template_exists("map_all.html"):
        return render_template(
            "map_missing.html",
            map_name="map_all.html",
            generate_command=(
                "python run_map_render.py --cluster_style --file_in plznito_all.json "
                "--file_out app/templates/map_all.html"
            ),
        )
    return render_template('index_map_all.html')


@app.route('/train_delays', methods=['GET'])
@cache.cached(timeout=60)
def get_delays():
    delays_r = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponline")
    delays_os = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponlineos")
    delays = {**delays_r, **delays_os}
    return jsonify(delays)

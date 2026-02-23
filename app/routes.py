import logging
from pathlib import Path

from flask import jsonify, render_template
from flask_caching import Cache

from app.train_delays import scrape_babitron_delays
from app import app

logger = logging.getLogger(__name__)

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

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

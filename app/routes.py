from flask import Flask, render_template, request
from flask import jsonify
from flask_caching import Cache
from train_delays import *
import logging
from app import app

logger = logging.getLogger(__name__)

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

cache = Cache(app, config={'CACHE_TYPE': 'simple'})
@app.route('/')
def index():
    return render_template('index.html')


@app.route('/plznito_map_all')
def plznito_map_all():
    return render_template('index_map_all.html')


@app.route('/train_delays', methods=['GET'])
@cache.cached(timeout=60)
def get_delays():
    delays_r = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponline")
    delays_os = scrape_babitron_delays("https://kam.mff.cuni.cz/~babilon/zponlineos")
    delays = {**delays_r, **delays_os}
    return jsonify(delays)

from flask import Flask, render_template, request
import logging
from app import app

logger = logging.getLogger(__name__)

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/plznito_map_all')
def plznito_map_all():
    return render_template('index_map_all.html')


@app.route('/annotation')
def annotation():
    # if key doesn't exist, returns None
    idx = request.args.get('id')
    value = request.args.get('value')

    with open("data", "a") as fout:
        fout.write(f"{idx} {value}\n")

    return f"<h1>Díky</h1>Pro značku {idx} uloženo {value}"

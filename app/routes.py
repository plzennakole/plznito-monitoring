from flask import Flask, render_template
import logging
from app import app

logging.basicConfig(filename='plznito_monitoring.log',
                    level=logging.INFO,
                    format='%(asctime)s %(message)s')


@app.route('/')
def index():
    return render_template('index.html')

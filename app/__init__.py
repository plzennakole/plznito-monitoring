from flask import Flask
import os
import logging

app = Flask(__name__)
app.config.update(SECRET_KEY=os.urandom(24))
app.logger.setLevel(logging.INFO)

from app import routes

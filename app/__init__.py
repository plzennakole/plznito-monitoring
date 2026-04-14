from flask import Flask
from jinja2 import ChoiceLoader, FileSystemLoader
import os
import logging
from pathlib import Path

app = Flask(__name__)
app.config.update(SECRET_KEY=os.urandom(24))
app.logger.setLevel(logging.INFO)

app.jinja_loader = ChoiceLoader([
    app.jinja_loader,
    FileSystemLoader(str(Path(__file__).parent.parent / "plznito_monitoring" / "templates")),
])

from app import routes

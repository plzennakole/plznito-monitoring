# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Flask app: Plznito maps and API, cycling counters and train delays."""

import logging
import os
from pathlib import Path

from flask import Flask
from flask_smorest import Api
from jinja2 import ChoiceLoader, FileSystemLoader

from app import settings  # noqa: F401 - loads .env first
from app.extensions import cache

app = Flask(__name__)
app.config.update(SECRET_KEY=os.urandom(24))
app.logger.setLevel(logging.INFO)
app.config.update(
    CACHE_TYPE='SimpleCache',
    API_TITLE="Plznito API",
    API_VERSION="1.0.0",
    OPENAPI_VERSION="3.0.3",
    OPENAPI_URL_PREFIX="/plznito/api",
    OPENAPI_JSON_PATH="openapi.json",
    OPENAPI_SWAGGER_UI_PATH="/docs",
    OPENAPI_SWAGGER_UI_URL="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/",
)

app.jinja_loader = ChoiceLoader([
    FileSystemLoader(str(Path(__file__).parent / "templates")),
    FileSystemLoader(str(Path(__file__).parent.parent / "plznito_monitoring" / "templates")),
])
cache.init_app(app)

from app import bikecounters, maps, routes, train_delays_api
from app.plznito_api import blp as plznito_api_blp

app.register_blueprint(maps.bp)
app.register_blueprint(train_delays_api.bp)
app.register_blueprint(bikecounters.bp)
api = Api(app)
api.register_blueprint(plznito_api_blp)

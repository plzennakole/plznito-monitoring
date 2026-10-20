from flask import Flask
from flask_smorest import Api
from jinja2 import ChoiceLoader, FileSystemLoader
import os
import logging
from pathlib import Path

app = Flask(__name__)
app.config.update(SECRET_KEY=os.urandom(24))
app.logger.setLevel(logging.INFO)
app.config.update(
    API_TITLE="Plznito API",
    API_VERSION="1.0.0",
    OPENAPI_VERSION="3.0.3",
    OPENAPI_URL_PREFIX="/plznito/api",
    OPENAPI_JSON_PATH="openapi.json",
    OPENAPI_SWAGGER_UI_PATH="/docs",
    OPENAPI_SWAGGER_UI_URL="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/",
)

app.jinja_loader = ChoiceLoader([
    app.jinja_loader,
    FileSystemLoader(str(Path(__file__).parent.parent / "plznito_monitoring" / "templates")),
])

from app import routes
from app.plznito_api import blp as plznito_api_blp

api = Api(app)
api.register_blueprint(plznito_api_blp)

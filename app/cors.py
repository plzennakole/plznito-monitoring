# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""CORS headers for the JSON APIs (configured by the historical ``TRAIN_DELAYS_CORS_*`` variables)."""

import os

from werkzeug.wrappers import Response

from app import settings  # noqa: F401 - loads .env before the variables below are read

CORS_ALLOW_ORIGIN = os.getenv('TRAIN_DELAYS_CORS_ALLOW_ORIGIN') or '*'
CORS_ALLOW_METHODS = os.getenv('TRAIN_DELAYS_CORS_ALLOW_METHODS') or 'GET, OPTIONS'
CORS_ALLOW_HEADERS = os.getenv('TRAIN_DELAYS_CORS_ALLOW_HEADERS') or 'Content-Type'
CORS_MAX_AGE = os.getenv('TRAIN_DELAYS_CORS_MAX_AGE') or '600'


def add_cors_headers(response: Response) -> Response:
    """
    ``after_request`` hook adding the CORS headers.

    :param response: Outgoing response.
    :return: The same response with CORS headers.
    """
    response.headers['Access-Control-Allow-Origin'] = CORS_ALLOW_ORIGIN
    response.headers['Access-Control-Allow-Methods'] = CORS_ALLOW_METHODS
    response.headers['Access-Control-Allow-Headers'] = CORS_ALLOW_HEADERS
    response.headers['Access-Control-Max-Age'] = CORS_MAX_AGE
    return response

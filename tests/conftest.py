# Copyright 2026 Plzeň na kole Inc. All rights reserved.
from collections.abc import Iterator

import pytest
from flask.testing import FlaskClient

from app import app, routes


@pytest.fixture
def client() -> Iterator[FlaskClient]:
    """
    Flask test client with an empty response cache before and after the test.

    :return: Test client for the app.
    """
    routes.cache.clear()
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client
    routes.cache.clear()

# Copyright 2026 Plzeň na kole Inc. All rights reserved.
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from app import routes


@pytest.mark.parametrize('path, expected_statuses', [
    ('/', {200}),
    ('/plznito/map-bike', {200, 503}),
    ('/plznito/map-all', {200, 503}),
    ('/bikecounters', {200}),
    ('/bikecounters/api/nav', {200}),
    ('/bikecounters/api/location/eco_prazdroj', {200}),
    ('/bikecounters/api/location/does_not_exist', {404}),
    ('/bikecounters/api/daily/eco_prazdroj', {200}),
    ('/bikecounters/api/weather', {200}),
])
def test_page_responds(client: FlaskClient, path: str, expected_statuses: set[int]) -> None:
    with patch.object(routes, 'query', return_value=[]):
        response = client.get(path)
    assert response.status_code in expected_statuses

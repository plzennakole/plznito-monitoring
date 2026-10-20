# Copyright 2026 Plzeň na kole Inc. All rights reserved.
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from app import bikecounters


@pytest.mark.parametrize(
    'path, expected_statuses',
    [
        ('/', {200}),
        ('/plznito/map-bike', {200, 503}),
        ('/plznito/map-all', {200, 503}),
        ('/bikecounters', {200}),
        ('/bikecounters/api/nav', {200}),
        ('/bikecounters/api/location/eco_prazdroj', {200}),
        ('/bikecounters/api/location/does_not_exist', {404}),
        ('/bikecounters/api/daily/eco_prazdroj', {200}),
        ('/bikecounters/api/weather', {200}),
    ],
)
def test_page_responds(client: FlaskClient, path: str, expected_statuses: set[int]) -> None:
    with patch.object(bikecounters, 'query', return_value=[]):
        response = client.get(path)
    assert response.status_code in expected_statuses


@pytest.mark.parametrize(
    'path, has_cors',
    [
        ('/train_delays/', True),
        ('/bikecounters/api/nav', True),
        ('/', False),
        ('/plznito/map-bike', False),
    ],
)
def test_cors_only_on_apis(client: FlaskClient, path: str, has_cors: bool) -> None:
    with (
        patch.object(bikecounters, 'query', return_value=[]),
        patch('app.train_delays_api.scrape_babitron_delays', return_value={}),
    ):
        response = client.get(path)
    assert ('Access-Control-Allow-Origin' in response.headers) is has_cors


def test_index_lists_every_get_route(client: FlaskClient) -> None:
    body = client.get('/').get_data(as_text=True)
    for path in ['/train_delays/', '/bikecounters/api/yoy/<loc_id>', '/plznito/api/issues', '/plznito/map-all']:
        assert path in body


def test_daily_is_counts_with_daily_resolution(client: FlaskClient) -> None:
    with patch.object(bikecounters, 'query', return_value=[]) as mock_query:
        daily = client.get('/bikecounters/api/daily/eco_prazdroj?resolution=hourly&from=2025-06-01')
        counts = client.get('/bikecounters/api/counts/eco_prazdroj?resolution=daily&from=2025-06-01')
    assert daily.get_json() == counts.get_json()
    assert daily.get_json()['resolution'] == 'daily'
    assert mock_query.call_args_list[0] == mock_query.call_args_list[2]

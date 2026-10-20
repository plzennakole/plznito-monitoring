# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import pathlib
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient
from jinja2 import ChoiceLoader, FileSystemLoader

from app import app, bikecounters
from plznito_monitoring import run_map_render as map_render


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


@pytest.mark.parametrize('path', ['/bikecounters/api/weather', '/bikecounters/api/yoy/eco_prazdroj'])
def test_bikecounter_endpoints_are_cached(client: FlaskClient, path: str) -> None:
    with patch.object(bikecounters, 'query', return_value=[]) as mock_query:
        first = client.get(path)
        calls_after_first = mock_query.call_count
        second = client.get(path)
    assert first.get_json() == second.get_json()
    assert calls_after_first >= 1
    assert mock_query.call_count == calls_after_first


def test_bikecounters_page_supports_conditional_requests(client: FlaskClient) -> None:
    last_modified = client.get('/bikecounters').headers['Last-Modified']
    assert client.get('/bikecounters', headers={'If-Modified-Since': last_modified}).status_code == 304


@pytest.fixture
def rendered_maps(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Serve small freshly rendered maps instead of the (git-ignored) generated ones."""
    item = {
        'id': 1,
        'name': "Díra",
        'status_id': 3,
        'latitude': '49.74',
        'longitude': '13.37',
        'created': {'date': '2025-01-10 10:00:00.000000'},
    }
    for name in ('plznito_map.html', 'plznito_map_all.html'):
        (tmp_path / name).write_text(map_render.get_map([item]), encoding='utf-8')
    monkeypatch.setattr(app, 'jinja_loader', ChoiceLoader([FileSystemLoader(str(tmp_path)), app.jinja_loader]))
    app.jinja_env.cache.clear()
    yield
    app.jinja_env.cache.clear()


@pytest.mark.parametrize('path', ['/plznito/map-bike', '/plznito/map-all'])
@pytest.mark.parametrize('query, has_header', [('', True), ('?embed=1', False)])
def test_map_pages_header_and_embed(
    client: FlaskClient, rendered_maps: None, path: str, query: str, has_header: bool
) -> None:
    response = client.get(path + query)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert ('class="pz-header"' in body) is has_header
    assert '<meta name="viewport"' in body
    assert 'id="plznito-app"' in body

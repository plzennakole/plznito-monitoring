# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import datetime
import json
import os
import pathlib
from collections.abc import Iterator
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from app import app, plznito_api
from tests.test_plznito_store import RECORDS, _api_record


@pytest.fixture
def client(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[FlaskClient]:
    db_path = tmp_path / 'plznito_all.json'
    db_path.write_text(json.dumps(RECORDS), encoding='utf-8')
    monkeypatch.setenv('PLZNITO_DB_JSON', str(db_path))
    monkeypatch.setattr(plznito_api, '_stores', {})
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client


def _ids(response) -> list[int]:
    return [item['id'] for item in response.get_json()['items']]


@pytest.mark.parametrize(
    'query, expected_ids, expected_total',
    [
        ('', [5, 3, 2, 1], 4),
        ('?cyklo=true', [3, 2], 2),
        ('?since=2026-03-15', [5, 3], 2),
        ('?since=2026-01-01&until=2026-03-14&status_id=2', [2], 1),
        ('?category_id=4', [5, 2, 1], 3),
        ('?since_id=2', [5, 3], 2),
        ('?limit=2', [5, 3], 4),
        ('?limit=2&offset=3', [1], 4),
        ('?offset=10', [], 4),
    ],
)
def test_list_issues(client: FlaskClient, query: str, expected_ids: list[int], expected_total: int) -> None:
    response = client.get(f'/plznito/api/issues{query}')
    assert response.status_code == 200
    assert _ids(response) == expected_ids
    assert response.get_json()['total'] == expected_total


def test_list_issues_item_shape(client: FlaskClient) -> None:
    item = client.get('/plznito/api/issues?limit=1').get_json()['items'][0]
    assert item['id'] == 5
    assert item['created'] == '2026-03-15T10:00:00+01:00'
    assert set(item) == {
        'id',
        'name',
        'description',
        'solution',
        'status_id',
        'status_label',
        'category_id',
        'category_label',
        'created',
        'latitude',
        'longitude',
        'address',
        'photo_url',
        'is_cyklo',
        'ticket_url',
    }


@pytest.mark.parametrize(
    'query',
    [
        '?limit=0',
        '?limit=1001',
        '?offset=-1',
        '?since=15.03.2026',
        '?cyklo=maybe',
        '?status_id=x',
    ],
)
def test_list_issues_invalid_params(client: FlaskClient, query: str) -> None:
    assert client.get(f'/plznito/api/issues{query}').status_code == 422


@pytest.mark.parametrize(
    'today, query, expected_ids',
    [
        (datetime.date(2026, 3, 15), '', [5, 3, 2]),
        (datetime.date(2026, 3, 15), '?days=0', [5, 3]),
        (datetime.date(2026, 3, 16), '?days=1&cyklo=true', [3]),
        (datetime.date(2027, 1, 1), '', []),
    ],
)
def test_list_new_issues(client: FlaskClient, today: datetime.date, query: str, expected_ids: list[int]) -> None:
    with patch.object(plznito_api, '_today', return_value=today):
        response = client.get(f'/plznito/api/issues/new{query}')
    assert response.status_code == 200
    assert _ids(response) == expected_ids


def test_list_new_issues_days_out_of_range(client: FlaskClient) -> None:
    assert client.get('/plznito/api/issues/new?days=366').status_code == 422


def test_get_issue(client: FlaskClient) -> None:
    response = client.get('/plznito/api/issues/3')
    assert response.status_code == 200
    assert response.get_json()['name'] == "Cyklostezka"


@pytest.mark.parametrize('issue_id', [4, 999])
def test_get_issue_not_found(client: FlaskClient, issue_id: int) -> None:
    response = client.get(f'/plznito/api/issues/{issue_id}')
    assert response.status_code == 404
    assert 'Cache-Control' not in response.headers


def test_meta(client: FlaskClient) -> None:
    body = client.get('/plznito/api/meta').get_json()
    assert body['total'] == 4
    assert body['cyklo_total'] == 2
    assert body['statuses'] == [
        {'id': 3, 'label': "Vyřešeno", 'count': 2},
        {'id': 6, 'label': "Odpovězeno", 'count': 1},
        {'id': 2, 'label': "V řešení", 'count': 1},
    ]
    assert body['categories'][0] == {'id': 4, 'label': "Kategorie 4", 'count': 3}


def test_cache_headers_and_conditional_request(client: FlaskClient) -> None:
    response = client.get('/plznito/api/issues')
    assert response.headers['Cache-Control'] == 'public, max-age=300'
    last_modified = response.headers['Last-Modified']

    response = client.get('/plznito/api/issues', headers={'If-Modified-Since': last_modified})
    assert response.status_code == 304


def test_data_reload_invalidates_conditional_request(client: FlaskClient) -> None:
    last_modified = client.get('/plznito/api/meta').headers['Last-Modified']

    db_path = pathlib.Path(plznito_api.db_path_from_env())
    db_path.write_text(json.dumps(RECORDS + [_api_record(6, '2026-04-01')]), encoding='utf-8')
    mtime = db_path.stat().st_mtime + 10
    os.utime(db_path, (mtime, mtime))

    response = client.get('/plznito/api/meta', headers={'If-Modified-Since': last_modified})
    assert response.status_code == 200
    assert response.get_json()['total'] == 5


@pytest.mark.parametrize('path', ['/plznito/api/openapi.json', '/plznito/api/docs'])
def test_docs_available(client: FlaskClient, path: str) -> None:
    assert client.get(path).status_code == 200


def test_openapi_lists_endpoints(client: FlaskClient) -> None:
    paths = client.get('/plznito/api/openapi.json').get_json()['paths']
    assert set(paths) == {
        '/plznito/api/issues',
        '/plznito/api/issues/new',
        '/plznito/api/issues/{issue_id}',
        '/plznito/api/meta',
    }


def test_api_sends_cors_headers(client: FlaskClient) -> None:
    assert client.get('/plznito/api/meta').headers['Access-Control-Allow-Origin']

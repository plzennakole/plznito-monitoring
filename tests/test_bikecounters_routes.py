# Copyright 2026 Plzeň na kole Inc. All rights reserved.
from unittest.mock import patch

import pytest
from flask.testing import FlaskClient

from app import routes


@pytest.mark.parametrize('query', [
    '?from=2025-13-45',
    '?to=2025-02-30',
    '?from=2025-1-1',
    '?from=2025-06-02&to=2025-06-01',
    '?from=2020-01-01&to=2025-01-01',
])
def test_api_counts_rejects_invalid_dates(client: FlaskClient, query: str) -> None:
    with patch.object(routes, 'query') as mock_query:
        response = client.get(f'/bikecounters/api/counts/eco_prazdroj{query}')
    assert response.status_code == 400
    mock_query.assert_not_called()


@pytest.mark.parametrize('query', ['', '?from=2025-06-01', '?to=2025-06-01', '?from=2025-06-01&to=2025-06-01'])
def test_api_counts_accepts_valid_dates(client: FlaskClient, query: str) -> None:
    with patch.object(routes, 'query', return_value=[]):
        response = client.get(f'/bikecounters/api/counts/eco_prazdroj{query}')
    assert response.status_code == 200
    assert response.get_json()['truncated'] is False


@pytest.mark.parametrize('combined_rows, per_collector_rows, expected', [
    (1, 1, False),
    (3, 1, True),
    (1, 3, True),
])
def test_api_counts_flags_truncated_result(
    client: FlaskClient, combined_rows: int, per_collector_rows: int, expected: bool,
) -> None:
    def fake_query(sql: str, params: list) -> list[dict]:
        if 'GROUP BY source_id, period' in sql:
            return [{'source_id': params[0], 'period': '2025-06-01', 'bikes': 1, 'scooters': 0}] * per_collector_rows
        return [{'period': '2025-06-01', 'bikes': 1, 'scooters': 0}] * combined_rows

    with patch.object(routes, '_MAX_RESULT_ROWS', 3), patch.object(routes, 'query', side_effect=fake_query):
        response = client.get('/bikecounters/api/counts/eco_prazdroj')
    assert response.get_json()['truncated'] is expected


def test_api_debug_location_without_collectors(client: FlaskClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BIKECOUNTERS_ENABLE_DEBUG_API', '1')
    with patch.object(routes, 'query') as mock_query:
        response = client.get('/bikecounters/api/debug/cam_malesice')
    assert response.status_code == 200
    assert response.get_json() == {'location': 'cam_malesice', 'sources': [], 'latest_rows': []}
    mock_query.assert_not_called()

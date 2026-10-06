import importlib.util
import pathlib
from datetime import date
from unittest.mock import patch

import pytest

from app import app
from app import routes

_YOY_PATH = pathlib.Path(__file__).parent.parent / 'bikecounters_web' / 'yoy.py'
_spec = importlib.util.spec_from_file_location('yoy', _YOY_PATH)
yoy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(yoy)


def _row(ts: str, bikes: int, scooters: int = 0) -> dict:
    return {'ts': ts, 'bikes': bikes, 'scooters': scooters}


# ── daily_totals ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize('rows, expected', [
    ([], {}),
    ([_row('2025-06-01', 10, 2)], {date(2025, 6, 1): 12}),
    ([_row('2025-06-01', 0, 0)], {}),
    ([_row('2025-06-01', 0, 3)], {date(2025, 6, 1): 3}),
    ([_row('2025-06-01 08', 5)], {date(2025, 6, 1): 5}),
    ([_row('2025-06-01', 1), _row('2025-06-02', 0), _row('2025-06-03', 4)],
     {date(2025, 6, 1): 1, date(2025, 6, 3): 4}),
])
def test_daily_totals(rows: list[dict], expected: dict) -> None:
    assert yoy.daily_totals(rows) == expected


# ── monthly_stats ─────────────────────────────────────────────────────────────

def test_monthly_stats_empty() -> None:
    assert yoy.monthly_stats({}) == []


def test_monthly_stats_groups_and_averages() -> None:
    daily = {
        date(2026, 1, 2): 30,
        date(2025, 12, 31): 10,
        date(2026, 1, 1): 20,
    }
    assert yoy.monthly_stats(daily) == [
        {'year': 2025, 'month': 12, 'days': 1, 'total': 10, 'avg': 10.0},
        {'year': 2026, 'month': 1, 'days': 2, 'total': 50, 'avg': 25.0},
    ]


# ── add_monthly_yoy ──────────────────────────────────────────────────────────

def _month(year: int, month: int, days: int, avg: float) -> dict:
    return {'year': year, 'month': month, 'days': days, 'total': days * avg, 'avg': avg}


@pytest.mark.parametrize('stats, min_days, expected_pct', [
    # Enough days in both years → change of avg/day.
    ([_month(2025, 6, 30, 100), _month(2026, 6, 30, 110)], 15, [None, 10.0]),
    ([_month(2025, 6, 30, 200), _month(2026, 6, 30, 150)], 15, [None, -25.0]),
    # Too few days in either year → not comparable.
    ([_month(2025, 6, 10, 100), _month(2026, 6, 30, 110)], 15, [None, None]),
    ([_month(2025, 6, 30, 100), _month(2026, 6, 14, 110)], 15, [None, None]),
    # Threshold is inclusive.
    ([_month(2025, 6, 15, 100), _month(2026, 6, 15, 110)], 15, [None, 10.0]),
    # Different month or a two-year gap is not a match.
    ([_month(2025, 5, 30, 100), _month(2026, 6, 30, 110)], 15, [None, None]),
    ([_month(2024, 6, 30, 100), _month(2026, 6, 30, 110)], 15, [None, None]),
    # Previous avg of zero cannot give a percentage.
    ([_month(2025, 6, 30, 0), _month(2026, 6, 30, 110)], 15, [None, None]),
    ([], 15, []),
])
def test_add_monthly_yoy(stats: list[dict], min_days: int, expected_pct: list) -> None:
    result = yoy.add_monthly_yoy(stats, min_days)
    assert [r['pct'] for r in result] == expected_pct


def test_add_monthly_yoy_does_not_mutate_input() -> None:
    stats = [_month(2025, 6, 30, 100)]
    yoy.add_monthly_yoy(stats)
    assert 'pct' not in stats[0]


# ── period_yoy ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize('daily, start, end, expected', [
    # Empty input.
    ({}, date(2026, 1, 1), date(2026, 1, 31),
     {'current': 0, 'previous': 0, 'days': 0, 'pct': None}),
    # Only matched days count; unmatched days in either year are ignored.
    ({
        date(2025, 3, 1): 100, date(2026, 3, 1): 120,  # matched
        date(2025, 3, 2): 100,                         # previous only
        date(2026, 3, 3): 500,                         # current only
    }, date(2026, 3, 1), date(2026, 3, 31),
     {'current': 120, 'previous': 100, 'days': 1, 'pct': 20.0}),
    # No overlap between years.
    ({date(2025, 5, 1): 100, date(2026, 6, 1): 100}, date(2026, 1, 1), date(2026, 12, 31),
     {'current': 0, 'previous': 0, 'days': 0, 'pct': None}),
    # Days outside the period are ignored.
    ({date(2025, 1, 1): 10, date(2026, 1, 1): 20, date(2026, 2, 1): 99, date(2025, 2, 1): 1},
     date(2026, 1, 1), date(2026, 1, 31),
     {'current': 20, 'previous': 10, 'days': 1, 'pct': 100.0}),
    # 29 February is skipped (no counterpart a year earlier).
    ({date(2028, 2, 29): 50, date(2028, 2, 28): 10, date(2027, 2, 28): 10},
     date(2028, 2, 1), date(2028, 2, 29),
     {'current': 10, 'previous': 10, 'days': 1, 'pct': 0.0}),
    # Start after end → empty period.
    ({date(2025, 1, 1): 10, date(2026, 1, 1): 20}, date(2026, 1, 2), date(2026, 1, 1),
     {'current': 0, 'previous': 0, 'days': 0, 'pct': None}),
])
def test_period_yoy(daily: dict, start: date, end: date, expected: dict) -> None:
    assert yoy.period_yoy(daily, start, end) == expected


# ── Routes ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client


def test_api_yoy_location_unknown_returns_404(client) -> None:
    with patch.object(routes, 'query') as mock_query:
        response = client.get('/bikecounters/api/yoy/does_not_exist')
    assert response.status_code == 404
    mock_query.assert_not_called()


def test_api_yoy_location_shape(client) -> None:
    rows = [_row('2025-06-01', 100), _row('2026-06-01', 110)]
    with patch.object(routes, 'query', return_value=rows), \
            patch.object(routes, '_yoy_ref_date', return_value=date(2026, 6, 30)):
        response = client.get('/bikecounters/api/yoy/eco_prazdroj')
    body = response.get_json()
    assert response.status_code == 200
    assert body['ref_date'] == '2026-06-30'
    assert body['ytd'] == {'current': 110, 'previous': 100, 'days': 1, 'pct': 10.0}
    assert body['mtd'] == body['ytd']
    assert [(m['year'], m['month']) for m in body['monthly']] == [(2025, 6), (2026, 6)]


def test_api_yoy_location_without_collectors(client) -> None:
    with patch.object(routes, 'query') as mock_query:
        response = client.get('/bikecounters/api/yoy/cam_malesice')
    body = response.get_json()
    assert response.status_code == 200
    assert body['monthly'] == []
    assert body['ytd']['pct'] is None
    mock_query.assert_not_called()


def test_api_yoy_all_skips_locations_without_collectors(client) -> None:
    routes.cache.clear()
    with patch.object(routes, 'query', return_value=[]):
        response = client.get('/bikecounters/api/yoy')
    ids = [loc['id'] for loc in response.get_json()['locations']]
    assert 'cam_malesice' not in ids
    assert 'eco_prazdroj' in ids
    routes.cache.clear()

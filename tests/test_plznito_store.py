# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import datetime
import json
import os
import pathlib
from unittest.mock import patch

import pytest

from app import plznito_store
from app.plznito_store import IssueNotFoundError, IssueStore, filter_issues, to_api_issue


def _api_record(
    item_id: int, created: str, status_id: int = 3, name: str = "Díra", report: str = "", category_id: int = 4
) -> dict:
    return {
        'id': item_id,
        'name': name,
        'report': report,
        'description': report,
        'solution': None,
        'status_id': status_id,
        'category_id': category_id,
        'latitude': '49.74',
        'longitude': '13.37',
        'address': ' Americká 1 ',
        'photos': [],
        'created': {'date': f"{created} 10:00:00.000000", 'timezone_type': 3, 'timezone': 'Europe/Prague'},
    }


def _scraped_record(item_id: int, date_text: str, status_id: int = 6, name: str = "Cyklostezka") -> dict:
    return {
        'id': item_id,
        'name': name,
        'report': "Rozbitá cyklostezka",
        'description': "Rozbitá cyklostezka",
        'solution': "",
        'status': "Zodpovězené",
        'status_id': status_id,
        'category': "Komunikace",
        'latitude': 49.75,
        'longitude': 13.38,
        'address': "",
        'photos': ['tickets/1.jpg'],
        'date': date_text,
    }


RECORDS = [
    _api_record(1, '2025-01-10'),
    _scraped_record(3, '15.03.2026'),
    _api_record(2, '2026-03-14', status_id=2, report="kolobežky na chodníku"),
    {'id': 4, 'name': "Bez data", 'status_id': 3, 'latitude': '49.7', 'longitude': '13.3'},
    _api_record(5, '2026-03-15', name="Recyklace", report="recyklace u cyklostezky"),
]


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / 'plznito_all.json'
    path.write_text(json.dumps(RECORDS), encoding='utf-8')
    return path


# ── to_api_issue ─────────────────────────────────────────────────────────────


def test_to_api_issue_api_shape() -> None:
    issue = to_api_issue(_api_record(1, '2025-01-10'))
    assert issue == {
        'id': 1,
        'name': "Díra",
        'description': "",
        'solution': "",
        'status_id': 3,
        'status_label': "Vyřešeno",
        'category_id': 4,
        'category_label': "Odpadky, černé skládky, úklidy",
        'created': datetime.datetime(2025, 1, 10, 10, 0, tzinfo=plznito_store.PLZNITO_TZ),
        'created_is_estimate': False,
        'latitude': 49.74,
        'longitude': 13.37,
        'address': "Americká 1",
        'photo_url': None,
        'is_cyklo': False,
        'ticket_url': 'https://www.plznito.cz/map/1',
    }


def test_to_api_issue_scraped_shape() -> None:
    issue = to_api_issue(_scraped_record(3, '15.03.2026'))
    assert issue['created'] == datetime.datetime(2026, 3, 15, tzinfo=plznito_store.PLZNITO_TZ)
    assert issue['status_label'] == "Odpovězeno"
    assert issue['category_id'] is None
    assert issue['category_label'] == "Komunikace"
    assert issue['address'] is None
    assert issue['photo_url'] == 'https://tf-prod-plznito-web.s3.eu-central-1.amazonaws.com/tickets/1.jpg'
    assert issue['is_cyklo'] is True


@pytest.mark.parametrize(
    'record',
    [
        {'id': 4, 'name': "Bez data", 'status_id': 3, 'latitude': '49.7', 'longitude': '13.3'},
        {**_api_record(1, '2025-01-10'), 'latitude': None},
        {**_api_record(1, '2025-01-10'), 'status_id': None},
        {**_api_record(1, '2025-01-10'), 'id': 'abc'},
        "not-a-dict",
    ],
)
def test_to_api_issue_invalid_records(record: object) -> None:
    assert to_api_issue(record) is None


@pytest.mark.parametrize('status_id, label', [(1, "Nové"), (4, "Odmítnuto"), (99, "Stav 99")])
def test_to_api_issue_status_label(status_id: int, label: str) -> None:
    assert to_api_issue(_api_record(1, '2025-01-10', status_id=status_id))['status_label'] == label


# ── IssueStore ───────────────────────────────────────────────────────────────


def test_store_skips_invalid_and_sorts_newest_first(db_path: pathlib.Path) -> None:
    issues = IssueStore(db_path).get_issues()
    assert [issue['id'] for issue in issues] == [5, 3, 2, 1]


def test_store_get_issue(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    assert store.get_issue(2)['status_id'] == 2
    with pytest.raises(IssueNotFoundError):
        store.get_issue(4)


def test_store_missing_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        IssueStore(tmp_path / 'missing.json').get_issues()


def test_store_loads_once_while_file_unchanged(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    with patch.object(plznito_store.json, 'load', wraps=json.load) as mock_load:
        store.get_issues()
        store.get_issues()
        store.get_issue(1)
        _ = store.updated_at
    assert mock_load.call_count == 1


def test_store_reloads_when_file_changes(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    assert len(store.get_issues()) == 4

    db_path.write_text(json.dumps(RECORDS + [_api_record(6, '2026-04-01')]), encoding='utf-8')
    mtime = db_path.stat().st_mtime + 10
    os.utime(db_path, (mtime, mtime))

    assert store.get_issues()[0]['id'] == 6
    assert store.updated_at == datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)


# ── filter_issues ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    'filters, expected_ids',
    [
        ({}, [5, 3, 2, 1]),
        ({'since': datetime.date(2026, 3, 15)}, [5, 3]),
        ({'until': datetime.date(2026, 3, 14)}, [2, 1]),
        ({'since': datetime.date(2026, 3, 14), 'until': datetime.date(2026, 3, 14)}, [2]),
        ({'since': datetime.date(2026, 3, 16), 'until': datetime.date(2026, 3, 1)}, []),
        ({'status_id': 3}, [5, 1]),
        ({'category_id': 4}, [5, 2, 1]),
        ({'cyklo': True}, [3, 2]),
        ({'cyklo': False}, [5, 1]),
        ({'since_id': 2}, [5, 3]),
        ({'since_id': 5}, []),
        ({'cyklo': True, 'status_id': 2}, [2]),
    ],
)
def test_filter_issues(db_path: pathlib.Path, filters: dict, expected_ids: list[int]) -> None:
    issues = IssueStore(db_path).get_issues()
    assert [issue['id'] for issue in filter_issues(issues, **filters)] == expected_ids


def test_store_estimates_creation_of_scraped_records(db_path: pathlib.Path) -> None:
    issues = {issue['id']: issue for issue in IssueStore(db_path).get_issues()}
    # Scraped ticket 3 (15.03.2026) is followed by ticket 5 created 2026-03-15 10:00.
    assert issues[3]['created_is_estimate'] is True
    assert issues[3]['created'].date() == datetime.date(2026, 3, 15)
    assert issues[5]['created_is_estimate'] is False

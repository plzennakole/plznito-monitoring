# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import contextlib
import datetime
import pathlib
from unittest.mock import patch

import pytest

from app import plznito_store
from app.plznito_store import IssueNotFoundError, IssueStore, filter_issues, to_api_issue
from plznito_monitoring import db


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


UPDATED_AT = datetime.datetime(2026, 4, 1, 6, 0, tzinfo=datetime.UTC)


def write_db(path: pathlib.Path, records: list, finished_at: datetime.datetime = UPDATED_AT) -> None:
    """Merge records into the DB at ``path`` and log a successful run finished at ``finished_at``."""
    with contextlib.closing(db.connect(path)) as conn:
        db.upsert_records(conn, records)
        db.finish_run(conn, db.start_run(conn), ok=True, stats={}, now=finished_at)


@pytest.fixture
def db_path(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / 'plznito.db'
    write_db(path, RECORDS)
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
        IssueStore(tmp_path / 'missing.db').get_issues()
    assert not (tmp_path / 'missing.db').exists()


def test_store_loads_once_while_db_unchanged(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    with patch.object(plznito_store.db, 'load_records', wraps=db.load_records) as mock_load:
        store.get_issues()
        store.get_issues()
        store.get_issue(1)
        _ = store.updated_at
    assert mock_load.call_count == 1


def test_store_reloads_after_successful_run(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    assert len(store.get_issues()) == 4
    assert store.updated_at == UPDATED_AT

    later = UPDATED_AT + datetime.timedelta(hours=6)
    write_db(db_path, [_api_record(6, '2026-04-01')], finished_at=later)

    assert store.get_issues()[0]['id'] == 6
    assert store.updated_at == later


def test_store_ignores_failed_run(db_path: pathlib.Path) -> None:
    store = IssueStore(db_path)
    store.get_issues()
    with contextlib.closing(db.connect(db_path)) as conn:
        db.finish_run(conn, db.start_run(conn), ok=False, stats={})
    with patch.object(plznito_store.db, 'load_records', wraps=db.load_records) as mock_load:
        assert store.updated_at == UPDATED_AT
    assert mock_load.call_count == 0


def test_store_of_never_updated_db(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'plznito.db'
    db.connect(path).close()
    store = IssueStore(path)
    assert store.get_issues() == []
    assert store.updated_at == plznito_store.NEVER_UPDATED


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

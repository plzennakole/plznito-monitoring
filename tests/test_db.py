# Copyright 2026 Plzeň na kole Inc. All rights reserved.
import contextlib
import datetime
import pathlib
import sqlite3
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from plznito_monitoring import db


@pytest.fixture
def conn(tmp_path: pathlib.Path) -> Iterator[sqlite3.Connection]:
    with contextlib.closing(db.connect(tmp_path / 'plznito.db')) as connection:
        yield connection


def test_connect_is_idempotent(tmp_path: pathlib.Path) -> None:
    path = tmp_path / 'plznito.db'
    for _ in range(2):
        with contextlib.closing(db.connect(path)) as connection:
            db.upsert_records(connection, [{'id': 1}])
    with contextlib.closing(db.connect(path)) as connection:
        assert db.count_records(connection) == 1


def test_connect_without_create_needs_existing_file(tmp_path: pathlib.Path) -> None:
    with pytest.raises(FileNotFoundError):
        db.connect(tmp_path / 'missing.db', create=False)
    assert not (tmp_path / 'missing.db').exists()


def test_connect_does_not_use_wal(conn: sqlite3.Connection) -> None:
    assert conn.execute('PRAGMA journal_mode').fetchone()[0] == 'delete'


@pytest.mark.parametrize(
    'old, new, expected',
    [
        ({}, {'id': 1, 'name': "a"}, {'id': 1, 'name': "a"}),
        ({'id': 1, 'name': "a"}, {'id': 1, 'name': "b"}, {'id': 1, 'name': "b"}),
        (
            {'id': 1, 'created': "x", 'address': "Americká 1"},
            {'id': 1, 'created': None, 'address': "", 'photos': [], 'extra': {}},
            {'id': 1, 'created': "x", 'address': "Americká 1"},
        ),
        ({'id': 1, 'status_id': 2}, {'id': 1, 'status_id': 0}, {'id': 1, 'status_id': 0}),
    ],
)
def test_merge_record(old: dict, new: dict, expected: dict) -> None:
    assert db.merge_record(old, new) == expected


def test_upsert_counts_new_updated_unchanged(conn: sqlite3.Connection) -> None:
    assert db.upsert_records(conn, [{'id': 1, 'name': "a"}, {'id': 2, 'name': "b"}]) == {
        'new': 2,
        'updated': 0,
        'unchanged': 0,
    }
    counts = db.upsert_records(conn, [{'id': 1, 'name': "a"}, {'id': 2, 'name': "B"}, {'id': 3}])
    assert counts == {'new': 1, 'updated': 1, 'unchanged': 1}
    assert db.load_records(conn) == [{'id': 1, 'name': "a"}, {'id': 2, 'name': "B"}, {'id': 3}]


def test_upsert_keeps_full_fields_when_scraped_record_arrives(conn: sqlite3.Connection) -> None:
    api_record = {
        'id': 7,
        'name': "Díra",
        'status_id': 2,
        'category_id': 4,
        'address': "Americká 1",
        'created': {'date': "2026-03-01 10:00:00.000000"},
    }
    scraped_record = {
        'id': 7,
        'name': "Díra",
        'status_id': 3,
        'status': "Vyřešené",
        'solution': "Opraveno",
        'address': "",
        'date': "01.03.2026",
    }
    db.upsert_records(conn, [api_record])
    db.upsert_records(conn, [scraped_record])

    (merged,) = db.load_records(conn)
    assert merged['status_id'] == 3
    assert merged['solution'] == "Opraveno"
    assert merged['created'] == api_record['created']
    assert merged['category_id'] == 4
    assert merged['address'] == "Americká 1"


def test_upsert_skips_malformed_and_later_duplicate_wins(conn: sqlite3.Connection) -> None:
    records = [{'id': 1, 'name': "first"}, {'id': '1', 'name': "second"}, {'name': "no id"}, {'id': None}, "bad"]
    assert db.upsert_records(conn, records) == {'new': 1, 'updated': 1, 'unchanged': 0}
    assert db.load_records(conn) == [{'id': '1', 'name': "second"}]


def test_upsert_tracks_first_seen_and_updated_at(conn: sqlite3.Connection) -> None:
    db.upsert_records(conn, [{'id': 1, 'name': "a"}])
    first = conn.execute('SELECT first_seen, updated_at FROM tickets').fetchone()
    db.upsert_records(conn, [{'id': 1, 'name': "a"}])
    assert tuple(conn.execute('SELECT first_seen, updated_at FROM tickets').fetchone()) == tuple(first)
    db.upsert_records(conn, [{'id': 1, 'name': "b"}])
    second = conn.execute('SELECT first_seen, updated_at FROM tickets').fetchone()
    assert second['first_seen'] == first['first_seen']
    assert second['updated_at'] > first['updated_at']


def test_upsert_error_rolls_back_everything(conn: sqlite3.Connection) -> None:
    db.upsert_records(conn, [{'id': 1, 'name': "a"}])
    with (
        patch.object(db, 'is_cyklo_item', side_effect=[False, RuntimeError("boom")]),
        pytest.raises(RuntimeError),
    ):
        db.upsert_records(conn, [{'id': 1, 'name': "changed"}, {'id': 2, 'name': "new"}])
    assert db.load_records(conn) == [{'id': 1, 'name': "a"}]
    assert not conn.in_transaction


@pytest.mark.parametrize(
    'cyklo, expected_ids',
    [(None, [1, 2, 3]), (True, [1, 3]), (False, [2])],
)
def test_load_records_cyklo_filter(conn: sqlite3.Connection, cyklo: bool | None, expected_ids: list[int]) -> None:
    db.upsert_records(
        conn,
        [
            {'id': 3, 'name': "Kolobežky na chodníku"},
            {'id': 1, 'report': "rozbitá cyklostezka"},
            {'id': 2, 'report': "recyklace", 'name': "Díra"},
        ],
    )
    assert [record['id'] for record in db.load_records(conn, cyklo=cyklo)] == expected_ids


def test_is_cyklo_follows_updates(conn: sqlite3.Connection) -> None:
    db.upsert_records(conn, [{'id': 1, 'name': "Díra"}])
    db.upsert_records(conn, [{'id': 1, 'name': "Díra na cyklostezce"}])
    assert [record['id'] for record in db.load_records(conn, cyklo=True)] == [1]


def test_empty_db(conn: sqlite3.Connection) -> None:
    assert db.max_id(conn) is None
    assert db.newest_record(conn) is None
    assert db.count_records(conn) == 0
    assert db.last_update(conn) is None


def test_max_id_and_newest_record(conn: sqlite3.Connection) -> None:
    db.upsert_records(conn, [{'id': 12, 'name': "b"}, {'id': 7, 'name': "a"}])
    assert db.max_id(conn) == 12
    assert db.newest_record(conn) == {'id': 12, 'name': "b"}


def test_last_update_ignores_failed_and_unfinished_runs(conn: sqlite3.Connection) -> None:
    ok_time = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.UTC)
    db.finish_run(conn, db.start_run(conn), ok=True, stats={'new': 1}, now=ok_time)
    db.finish_run(conn, db.start_run(conn), ok=False, stats={}, now=ok_time + datetime.timedelta(hours=6))
    db.start_run(conn)
    assert db.last_update(conn) == ok_time

    rows = conn.execute('SELECT ok, finished_at IS NOT NULL AS finished, stats FROM runs ORDER BY id').fetchall()
    assert [(row['ok'], row['finished']) for row in rows] == [(1, 1), (0, 1), (0, 0)]
    assert rows[0]['stats'] == '{"new": 1}'


def test_backup_is_a_readable_copy(conn: sqlite3.Connection, tmp_path: pathlib.Path) -> None:
    db.upsert_records(conn, [{'id': 1, 'name': "a"}])
    db.backup(conn, tmp_path / 'backup.db')
    db.upsert_records(conn, [{'id': 2}])
    with contextlib.closing(db.connect(tmp_path / 'backup.db', create=False)) as copy:
        assert db.load_records(copy) == [{'id': 1, 'name': "a"}]

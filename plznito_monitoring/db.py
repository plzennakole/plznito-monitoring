# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""SQLite storage of Plznito tickets: raw records by id, plus a log of update runs."""

import contextlib
import datetime
import json
import logging
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

from plznito_monitoring.common import parse_int
from plznito_monitoring.cyklo_filter import is_cyklo_item

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).parent / 'plznito.db'
# The cron job writes for a few seconds every few hours; readers wait for it instead of failing.
BUSY_TIMEOUT_MS = 30000

# Raw records are kept as JSON: the two record shapes (API and web scrape) need no column mapping,
# and a change of the scraped format needs no schema change.
SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets (
    id          INTEGER PRIMARY KEY,
    data        TEXT    NOT NULL,
    is_cyklo    INTEGER NOT NULL,
    first_seen  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tickets_cyklo ON tickets(is_cyklo);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  TEXT    NOT NULL,
    finished_at TEXT,
    ok          INTEGER NOT NULL DEFAULT 0,
    stats       TEXT
);
"""

Record = dict[str, Any]


def connect(path: str | Path, create: bool = True) -> sqlite3.Connection:
    """
    Open the ticket DB.

    The default rollback journal is used, not WAL: WAL needs shared memory, which is unreliable
    on network file systems such as PythonAnywhere's home directories.

    :param path: DB file path.
    :param create: Create the file and the tables if missing (writers); readers pass ``False``.
    :raises FileNotFoundError: If ``create`` is ``False`` and the file does not exist.
    :return: Connection; close it with :func:`contextlib.closing`.
    """
    if not create and not Path(path).is_file():
        raise FileNotFoundError(f"Plznito DB {path} does not exist.")
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    if create:
        conn.executescript(SCHEMA)
    return conn


def _now_iso() -> str:
    """
    :return: Current UTC time as ISO 8601 text.
    """
    return datetime.datetime.now(datetime.UTC).isoformat()


def _dump(record: Record) -> str:
    """
    :param record: Raw record.
    :return: Compact JSON text.
    """
    return json.dumps(record, ensure_ascii=False, separators=(',', ':'))


def collect_valid_records(records: list[Any], source_name: str) -> list[tuple[int, Record]]:
    """
    Pair each record with its integer id, dropping non-dicts and records without a valid id.

    :param records: Raw records.
    :param source_name: Label used in log messages.
    :return: ``(id, record)`` pairs in input order.
    """
    valid_records = []
    skipped_invalid = 0

    for record in records:
        if not isinstance(record, dict):
            skipped_invalid += 1
            logger.debug("Skipping non-dict record from %s: %r", source_name, record)
            continue

        normalized_id = parse_int(record.get('id'))
        if normalized_id is None:
            skipped_invalid += 1
            logger.debug("Skipping record without valid id from %s: %r", source_name, record)
            continue
        valid_records.append((normalized_id, record))

    if skipped_invalid:
        logger.warning(
            "Skipped %d malformed records from %s (missing/invalid id).",
            skipped_invalid,
            source_name,
        )
    return valid_records


def merge_record(old: Record, new: Record) -> Record:
    """
    Merge a new version of a record into the stored one.

    New non-empty fields overwrite old ones, other old fields are kept, so a lightweight
    scraped record does not wipe ``created``, ``category_id`` etc. of a full API record.

    :param old: Stored record (``{}`` for a new ticket).
    :param new: Incoming record.
    :return: Merged record.
    """
    new_fields = {key: value for key, value in new.items() if value not in (None, "", [], {})}
    return {**old, **new_fields}


def upsert_records(conn: sqlite3.Connection, records: list[Any], source_name: str = "incoming update") -> Counter[str]:
    """
    Merge records into the DB by id (see :func:`merge_record`), all in one transaction.

    Rows are written only when the merged record differs from the stored one. On any error
    nothing is written. Later duplicates in ``records`` win.

    :param conn: Connection from :func:`connect`.
    :param records: Raw records; malformed ones are skipped with a warning.
    :param source_name: Label used in log messages.
    :return: Counts of ``new``, ``updated`` and ``unchanged`` records.
    """
    counts: Counter[str] = Counter(new=0, updated=0, unchanged=0)
    now = _now_iso()
    with conn:
        # Lock for writing before reading the old rows, so a concurrent run cannot slip in between.
        conn.execute('BEGIN IMMEDIATE')
        for record_id, record in collect_valid_records(records, source_name):
            row = conn.execute('SELECT data FROM tickets WHERE id = ?', (record_id,)).fetchone()
            old = json.loads(row['data']) if row is not None else {}
            merged = merge_record(old, record)
            if row is None:
                counts['new'] += 1
                conn.execute(
                    'INSERT INTO tickets (id, data, is_cyklo, first_seen, updated_at) VALUES (?, ?, ?, ?, ?)',
                    (record_id, _dump(merged), is_cyklo_item(merged), now, now),
                )
            elif merged != old:
                counts['updated'] += 1
                conn.execute(
                    'UPDATE tickets SET data = ?, is_cyklo = ?, updated_at = ? WHERE id = ?',
                    (_dump(merged), is_cyklo_item(merged), now, record_id),
                )
            else:
                counts['unchanged'] += 1
    return counts


def load_records(conn: sqlite3.Connection, cyklo: bool | None = None) -> list[Record]:
    """
    All stored records, ordered by id.

    :param conn: Connection from :func:`connect`.
    :param cyklo: Only cycling (``True``) or only non-cycling (``False``) records; ``None`` for all.
    :return: Raw records.
    """
    if cyklo is None:
        rows = conn.execute('SELECT data FROM tickets ORDER BY id')
    else:
        rows = conn.execute('SELECT data FROM tickets WHERE is_cyklo = ? ORDER BY id', (cyklo,))
    return [json.loads(row['data']) for row in rows]


def count_records(conn: sqlite3.Connection) -> int:
    """
    :param conn: Connection from :func:`connect`.
    :return: Number of stored tickets.
    """
    count: int = conn.execute('SELECT COUNT(*) FROM tickets').fetchone()[0]
    return count


def newest_record(conn: sqlite3.Connection) -> Record | None:
    """
    :param conn: Connection from :func:`connect`.
    :return: The record with the highest id, or ``None`` for an empty DB.
    """
    row = conn.execute('SELECT data FROM tickets ORDER BY id DESC LIMIT 1').fetchone()
    if row is None:
        return None
    record: Record = json.loads(row['data'])
    return record


def max_id(conn: sqlite3.Connection) -> int | None:
    """
    :param conn: Connection from :func:`connect`.
    :return: Highest stored ticket id, or ``None`` for an empty DB.
    """
    value: int | None = conn.execute('SELECT MAX(id) FROM tickets').fetchone()[0]
    return value


def start_run(conn: sqlite3.Connection) -> int:
    """
    Record the start of an update run.

    :param conn: Connection from :func:`connect`.
    :return: Run id for :func:`finish_run`.
    """
    with conn:
        cursor = conn.execute('INSERT INTO runs (started_at) VALUES (?)', (_now_iso(),))
    if cursor.lastrowid is None:
        raise sqlite3.DatabaseError("Inserting a run row gave no row id.")
    return cursor.lastrowid


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    ok: bool,
    stats: dict[str, Any],
    now: datetime.datetime | None = None,
) -> None:
    """
    Record the end of an update run.

    :param conn: Connection from :func:`connect`.
    :param run_id: Id from :func:`start_run`.
    :param ok: Whether the run succeeded; only successful runs count as data updates.
    :param stats: JSON-serialisable run statistics.
    :param now: Finish time (UTC); defaults to now.
    """
    finished_at = now.isoformat() if now is not None else _now_iso()
    with conn:
        conn.execute(
            'UPDATE runs SET finished_at = ?, ok = ?, stats = ? WHERE id = ?',
            (finished_at, ok, json.dumps(stats, ensure_ascii=False, default=str), run_id),
        )


def last_update(conn: sqlite3.Connection) -> datetime.datetime | None:
    """
    :param conn: Connection from :func:`connect`.
    :return: Finish time (UTC) of the last successful run, or ``None`` if there was none.
    """
    value = conn.execute('SELECT MAX(finished_at) FROM runs WHERE ok = 1').fetchone()[0]
    return datetime.datetime.fromisoformat(value) if value is not None else None


def backup(conn: sqlite3.Connection, path: str | Path) -> None:
    """
    Write a consistent copy of the DB, also while others read or write it.

    :param conn: Connection from :func:`connect`.
    :param path: Target file; replaced if it exists. Its directory must exist.
    """
    with contextlib.closing(sqlite3.connect(path)) as target:
        conn.backup(target)

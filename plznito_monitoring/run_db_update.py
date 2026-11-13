# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""
Update the Plznito SQLite DB with the recent tickets from the plznito.cz API.

Other modes: rebuild from snapshots (``--restore``), import or export a JSON list
(``--import-json``, ``--export-json``) and write a backup copy (``--backup``).
"""

import argparse
import bz2
import contextlib
import gzip
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from plznito_monitoring import db
from plznito_monitoring.common import atomic_write_json, load_json, setup_logging
from plznito_monitoring.restore_all import API_LIST_URL, PlznitoApiError, download_one_id, download_recent
from plznito_monitoring.run_map_render import _parse_item_datetime

logger = logging.getLogger(__name__)
# Defaults live next to this file, so the scripts work from any working directory.
PLZNITO_DIR = Path(__file__).parent
ID_WINDOW_BACK_DEFAULT = 100
SEED_DATA_DIR_DEFAULT = str(PLZNITO_DIR / "data")
LOG_FILE_DEFAULT = str(PLZNITO_DIR / "plznito_monitoring.log")
SNAPSHOT_DIR_DEFAULT = str(PLZNITO_DIR / "notebooks")
DOWNLOAD_WORKERS = 5
# The ticket list covers this many days, capped by the API at the newest ~500 tickets.
RECENT_DAYS = 90
# After an outage the DB can be far behind; one run catches up at most this many ids.
MAX_CRAWL_IDS = 20000
# Warn when a run finds no new ticket and the newest one is older than this.
STALE_AFTER_DAYS = 3
PROGRESS_LOG_EVERY = 1000

Record = dict[str, Any]


class InvalidPayloadError(ValueError):
    """Raised when a JSON file or snapshot does not have the expected shape."""


def _save_raw_snapshot(data: dict[str, list[Any]], out_dirname: str) -> None:
    """
    Save the raw update payload as a timestamped ``.json.bz2`` file.

    :param data: Update payload.
    :param out_dirname: Existing target directory.
    """
    snapshot_filename = datetime.today().strftime("%Y%m%dT%H%M%S") + ".json.bz2"
    snapshot_path = os.path.join(out_dirname or ".", snapshot_filename)
    with bz2.open(snapshot_path, "wt", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info("Saved raw update snapshot to %s.", snapshot_path)


def _extract_item(download_output: Any) -> Record | None:
    """
    Return the non-empty ``item`` of a download result.

    :param download_output: Result of :func:`download_one_id`.
    :return: The item, or ``None`` for failed or empty downloads.
    """
    if not isinstance(download_output, dict):
        return None
    item = download_output.get("item")
    if not isinstance(item, dict) or not item:
        return None
    return item


def get_plznito_current_data(db_max_id: int | None, id_window_back: int = ID_WINDOW_BACK_DEFAULT) -> dict[str, Any]:
    """
    Download the recent tickets: the API ticket list, plus the detail of every id it misses.

    The ids from ``db_max_id - id_window_back`` to the newest listed id are covered; the list also
    refreshes older tickets it contains. Ids missing from the list (older than the list, or deleted)
    are downloaded one by one, at most :data:`MAX_CRAWL_IDS` ids per run. Any failed download raises
    before anything is stored, so the next run retries the same range instead of moving the DB past
    a ticket it never got.

    :param db_max_id: Highest ticket id in the DB, ``None`` for an empty DB (then only the list is used).
    :param id_window_back: How many ids before ``db_max_id`` to re-check (status updates).
    :raises ValueError: If ``id_window_back`` is negative or not below :data:`MAX_CRAWL_IDS`.
    :raises PlznitoApiError: If the ticket list is empty (the API probably changed) or a payload is unexpected.
    :raises requests.RequestException: If a download fails.
    :return: Update payload ``{"items": [...], "stats": {...}}``.
    """
    if not 0 <= id_window_back < MAX_CRAWL_IDS:
        raise ValueError(f"id_window_back must be >= 0 and < {MAX_CRAWL_IDS}.")

    listed = db.collect_valid_records(download_recent(RECENT_DAYS), "ticket list")
    if not listed:
        raise PlznitoApiError(
            f"The plznito.cz ticket list ({API_LIST_URL}) returned no tickets; the API has probably changed."
        )
    listed_ids = [record_id for record_id, _ in listed]
    newest_listed = max(listed_ids)
    start_id = min(listed_ids) if db_max_id is None else max(1, db_max_id - id_window_back)
    end_id = newest_listed
    if end_id - start_id > MAX_CRAWL_IDS:
        end_id = start_id + MAX_CRAWL_IDS
    if db_max_id is not None and newest_listed - db_max_id > len(listed):
        logger.warning(
            "DB is %d tickets behind plznito.cz (newest listed id %d, DB max id %d); catching up ids %d-%d.",
            newest_listed - db_max_id,
            newest_listed,
            db_max_id,
            start_id,
            end_id,
        )

    # Listed tickets beyond a capped range are left for the next run, so the DB never skips ids.
    items_by_id = {record_id: record for record_id, record in listed if record_id <= end_id}
    missing_ids = [ticket_id for ticket_id in range(start_id, end_id + 1) if ticket_id not in items_by_id]
    logger.info(
        "Ticket list has %d tickets (ids %d-%d); downloading %d missing ids of the range %d-%d.",
        len(listed),
        min(listed_ids),
        newest_listed,
        len(missing_ids),
        start_id,
        end_id,
    )

    empty_items = 0
    with ThreadPoolExecutor(max_workers=DOWNLOAD_WORKERS) as executor:
        results = executor.map(download_one_id, missing_ids)
        for done, (ticket_id, output) in enumerate(zip(missing_ids, results, strict=True), start=1):
            if done % PROGRESS_LOG_EVERY == 0:
                logger.info("Download progress: id %d (%d/%d).", ticket_id, done, len(missing_ids))
            item = _extract_item(output)
            if item is None:
                empty_items += 1
                continue
            items_by_id[ticket_id] = item

    items = [items_by_id[record_id] for record_id in sorted(items_by_id)]
    stats: dict[str, Any] = {
        'start_id': start_id,
        'end_id': end_id,
        'listed': len(listed),
        'newest_listed': newest_listed,
        'requests': 1 + len(missing_ids),
        'empty': empty_items,
    }
    return {"items": items, "stats": stats}


def _log_update_summary(
    conn: sqlite3.Connection, data_current: dict[str, Any], counts: Counter[str], db_path: str, started: float
) -> None:
    """
    One INFO line describing the run, plus a WARNING when no new tickets seem to arrive any more.

    :param conn: DB connection.
    :param data_current: Downloaded payload with ``stats``.
    :param counts: Result of :func:`plznito_monitoring.db.upsert_records`.
    :param db_path: DB file path.
    :param started: ``time.monotonic()`` at the start of the run.
    """
    stats = data_current.get("stats", {})
    newest_id = db.max_id(conn)
    newest = db.newest_record(conn)
    newest_date = _parse_item_datetime(newest)[0] if newest is not None else None
    newest_text = "none"
    if newest_id is not None:
        newest_text = f"#{newest_id}" + (f" from {newest_date:%d.%m.%Y}" if newest_date else "")
    logger.info(
        "Update OK in %.1f s: ids %s-%s, %s listed, %s HTTP requests, %d tickets downloaded "
        "(%d new, %d updated, %d unchanged), %s ids without a ticket; %s now has %d records, newest %s.",
        time.monotonic() - started,
        stats.get('start_id'),
        stats.get('end_id'),
        stats.get('listed'),
        stats.get('requests'),
        counts.total(),
        counts['new'],
        counts['updated'],
        counts['unchanged'],
        stats.get('empty'),
        db_path,
        db.count_records(conn),
        newest_text,
    )
    if counts['new'] == 0 and newest_id is not None and newest_date is not None:
        age_days = (datetime.now() - newest_date).days
        if age_days > STALE_AFTER_DAYS:
            logger.warning(
                "No new tickets: the newest ticket #%d is %d days old. Check that plznito.cz is reachable and "
                "its API has not changed (python -m plznito_monitoring.scrape_one %d).",
                newest_id,
                age_days,
                newest_id + 1,
            )


def _load_record_list(path: str) -> list[Any]:
    """
    Load a JSON list of records, e.g. an old ``plznito_all.json``.

    :param path: File path.
    :raises InvalidPayloadError: If the file does not contain a JSON list.
    :return: Records.
    """
    data = load_json(path)
    if not isinstance(data, list):
        raise InvalidPayloadError(f"{path} must contain a JSON list.")
    return data


def _load_snapshot_payload(full_fname: str) -> Any:
    """
    Load a ``.json`` or ``.json.bz2`` snapshot.

    :param full_fname: Snapshot path.
    :return: Parsed JSON, or ``None`` for other file types.
    """
    if full_fname.endswith(".json"):
        return load_json(full_fname)
    if full_fname.endswith(".bz2"):
        with bz2.open(full_fname, "rt", encoding="utf-8") as fr:
            return json.load(fr)
    return None


def _snapshot_items(payload: Any) -> list[Any]:
    """
    Records of a snapshot: ``{"items": [...]}`` (update snapshots, old seed files)
    or ``{"item": {...}}`` (per-id seed files).

    :param payload: Parsed snapshot.
    :raises InvalidPayloadError: For any other shape, including empty ``item``.
    :return: Records.
    """
    if isinstance(payload, dict) and isinstance(payload.get("items"), list):
        items: list[Any] = payload["items"]
        return items
    if isinstance(payload, dict) and isinstance(payload.get("item"), dict) and payload["item"]:
        return [payload["item"]]
    raise InvalidPayloadError("Expected snapshot with 'items' list or non-empty 'item' object.")


@contextlib.contextmanager
def _recorded_run(conn: sqlite3.Connection, mode: str) -> Iterator[dict[str, Any]]:
    """
    Log a run in the ``runs`` table: ``ok`` only if the block finishes without an exception.

    :param conn: DB connection.
    :param mode: Run kind, stored in the stats.
    :return: Context manager yielding the stats dict, which the block may fill in.
    """
    run_id = db.start_run(conn)
    stats: dict[str, Any] = {'mode': mode}
    ok = False
    try:
        yield stats
        ok = True
    finally:
        db.finish_run(conn, run_id, ok=ok, stats=stats)


def db_restore(db_path: str, data_dirname: str = ".") -> None:
    """
    Merge all snapshots in ``data_dirname`` into the DB.

    :param db_path: DB file (created if missing).
    :param data_dirname: Directory with ``.json`` / ``.json.bz2`` snapshots, applied in name order.
    """
    items = []
    skipped = 0
    for fname in sorted(os.listdir(data_dirname)):
        full_fname = os.path.join(data_dirname, fname)
        if not (full_fname.endswith(".json") or full_fname.endswith(".bz2")):
            continue

        try:
            items.extend(_snapshot_items(_load_snapshot_payload(full_fname)))
        except (OSError, ValueError) as exc:
            skipped += 1
            logger.debug("Skipping snapshot %s due to invalid payload: %s", full_fname, exc)
    if skipped:
        logger.warning("Skipped %d invalid or empty snapshots in %s.", skipped, data_dirname)

    with contextlib.closing(db.connect(db_path)) as conn, _recorded_run(conn, "restore") as stats:
        stats.update(db.upsert_records(conn, items, f"snapshots in {data_dirname}"))
        logger.info("Restore completed: %s; %s now has %d records.", stats, db_path, db.count_records(conn))


def db_import_json(db_path: str, json_path: str) -> None:
    """
    Merge a JSON list of records (e.g. an old ``plznito_all.json``) into the DB.

    :param db_path: DB file (created if missing).
    :param json_path: JSON file with a list of records.
    """
    records = _load_record_list(json_path)
    with contextlib.closing(db.connect(db_path)) as conn, _recorded_run(conn, "import") as stats:
        stats.update(db.upsert_records(conn, records, json_path))
        logger.info("Imported %s: %s; %s now has %d records.", json_path, stats, db_path, db.count_records(conn))


def db_export_json(db_path: str, json_path: str) -> None:
    """
    Write all records of the DB as a JSON list, ordered by id.

    :param db_path: Existing DB file.
    :param json_path: Output JSON file.
    """
    with contextlib.closing(db.connect(db_path, create=False)) as conn:
        records = db.load_records(conn)
    atomic_write_json(json_path, records)
    logger.info("Exported %d records to %s.", len(records), json_path)


def db_backup(db_path: str, backup_path: str) -> None:
    """
    Write a consistent copy of the DB, gzip-compressed if ``backup_path`` ends with ``.gz``.

    A compressed copy is about 8x smaller. It is written next to the target and then moved over it,
    so an interrupted run never leaves a truncated backup in place of the previous one.

    :param db_path: Existing DB file.
    :param backup_path: Target file; its directory must exist.
    """
    with contextlib.closing(db.connect(db_path, create=False)) as conn:
        if not backup_path.endswith('.gz'):
            db.backup(conn, backup_path)
        else:
            with tempfile.TemporaryDirectory(dir=os.path.dirname(os.path.abspath(backup_path))) as tmp_dir:
                plain_path = os.path.join(tmp_dir, 'backup.db')
                gz_path = os.path.join(tmp_dir, 'backup.db.gz')
                db.backup(conn, plain_path)
                with open(plain_path, 'rb') as fr, gzip.open(gz_path, 'wb', compresslevel=6) as fw:
                    shutil.copyfileobj(fr, fw)
                os.replace(gz_path, backup_path)
    logger.info("Backed up %s to %s (%.1f MB).", db_path, backup_path, os.path.getsize(backup_path) / 1e6)


def db_update(
    db_path: str,
    out_dirname: str = "",
    save_update_data: bool = False,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
) -> None:
    """
    Download recent tickets and merge them into the DB in one transaction.

    :param db_path: DB file to update (created if missing).
    :param out_dirname: Directory for the raw snapshot (``save_update_data``).
    :param save_update_data: Also save the raw downloaded payload.
    :param id_window_back: How many ids before the DB's highest id to re-check.
    """
    started = time.monotonic()
    with contextlib.closing(db.connect(db_path)) as conn, _recorded_run(conn, "update") as stats:
        data_current = get_plznito_current_data(db.max_id(conn), id_window_back=id_window_back)
        stats.update(data_current.get("stats", {}))

        if save_update_data:
            _save_raw_snapshot(data_current, out_dirname)

        counts = db.upsert_records(conn, data_current["items"])
        stats.update(counts)
        _log_update_summary(conn, data_current, counts, db_path, started)


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default=str(db.DEFAULT_DB_PATH))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--restore", action="store_true", help="Merge the snapshots in --seed-data-dir, no download.")
    mode.add_argument("--import-json", type=str, default=None, help="Merge a JSON list of records, no download.")
    mode.add_argument("--export-json", type=str, default=None, help="Write all records as a JSON list.")
    mode.add_argument("--backup", type=str, default=None, help="Write a DB copy here, gzipped if *.gz.")
    parser.add_argument("--save_update_data", action="store_true")
    parser.add_argument("--id-window-back", type=int, default=ID_WINDOW_BACK_DEFAULT)
    parser.add_argument("--seed-data-dir", type=str, default=SEED_DATA_DIR_DEFAULT)
    args = parser.parse_args()

    setup_logging(LOG_FILE_DEFAULT)
    logger.info("Running with args: %s", args)
    try:
        _run(args)
    except Exception:
        # Top-level crash handler: the traceback goes to the log file and to stderr (cron/deploy log).
        logger.exception("Plznito update failed.")
        raise SystemExit(1) from None


def _run(args: argparse.Namespace) -> None:
    """
    Run the mode selected by the command line.

    :param args: Parsed arguments.
    """
    if args.restore:
        db_restore(args.db, data_dirname=args.seed_data_dir)
    elif args.import_json:
        db_import_json(args.db, args.import_json)
    elif args.export_json:
        db_export_json(args.db, args.export_json)
    elif args.backup:
        db_backup(args.db, args.backup)
    else:
        if args.save_update_data:
            os.makedirs(SNAPSHOT_DIR_DEFAULT, exist_ok=True)
        db_update(
            args.db,
            out_dirname=SNAPSHOT_DIR_DEFAULT,
            save_update_data=args.save_update_data,
            id_window_back=args.id_window_back,
        )


if __name__ == "__main__":
    main()

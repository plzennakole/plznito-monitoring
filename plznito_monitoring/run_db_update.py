# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""
Update the Plznito SQLite DB by scraping recent tickets.

Other modes: rebuild from snapshots (``--restore``), import or export a JSON list
(``--import-json``, ``--export-json``) and write a backup copy (``--backup``).
"""

import argparse
import bz2
import contextlib
import json
import logging
import os
import sqlite3
import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from plznito_monitoring import db
from plznito_monitoring.common import atomic_write_json, load_json, parse_int, setup_logging
from plznito_monitoring.restore_all import download_one_id, scrape_map_page
from plznito_monitoring.run_map_render import _parse_item_datetime

logger = logging.getLogger(__name__)
# Defaults live next to this file, so the scripts work from any working directory.
PLZNITO_DIR = Path(__file__).parent
ID_WINDOW_BACK_DEFAULT = 100
ID_LOOKAHEAD_DEFAULT = 200
SEED_DATA_DIR_DEFAULT = str(PLZNITO_DIR / "data")
LOG_FILE_DEFAULT = str(PLZNITO_DIR / "plznito_monitoring.log")
SNAPSHOT_DIR_DEFAULT = str(PLZNITO_DIR / "notebooks")
MAX_CONSECUTIVE_SCRAPE_FAILURES = 10
# Ids are fetched in ordered chunks, so a crawl stop wastes at most one chunk of requests.
SCRAPE_CHUNK_SIZE = 10
SCRAPE_WORKERS = 5
# After an outage the DB can be far behind; one run catches up at most this many ids.
MAX_CRAWL_IDS = 20000
# Warn when a run finds no new ticket and the newest one is older than this.
STALE_AFTER_DAYS = 3
PROGRESS_LOG_EVERY = 1000

Record = dict[str, Any]


class InvalidPayloadError(ValueError):
    """Raised when a JSON file or snapshot does not have the expected shape."""


class AnchorResolutionError(ValueError):
    """Raised when no ticket id is known to start the crawl from."""


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


def _max_seed_json_id(seed_data_dir: str) -> int | None:
    """
    Highest ticket id among ``<id>.json`` files in the seed directory.

    :param seed_data_dir: Directory with per-id seed files.
    :return: Highest id, or ``None`` if the directory is missing or has no such files.
    """
    if not seed_data_dir:
        return None
    if not os.path.isdir(seed_data_dir):
        logger.warning("Seed data directory %s does not exist.", seed_data_dir)
        return None

    file_ids = [
        int(stem)
        for stem, ext in (os.path.splitext(fname) for fname in os.listdir(seed_data_dir))
        if ext == ".json" and stem.isdigit()
    ]
    return max(file_ids, default=None)


def _resolve_anchor_id(
    db_max_id: int | None,
    anchor_id: int | None = None,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> tuple[int, str]:
    """
    Pick the ticket id to crawl around: the DB's highest id, else the CLI value, else the seed directory.

    :param db_max_id: Highest ticket id in the DB, ``None`` for an empty DB.
    :param anchor_id: Anchor given on the command line.
    :param seed_data_dir: Directory with per-id seed files.
    :raises AnchorResolutionError: If none of the sources gives an id.
    :return: ``(anchor id, source name)``.
    """
    if db_max_id is not None:
        return db_max_id, "db"

    cli_anchor = parse_int(anchor_id)
    if cli_anchor is not None:
        return cli_anchor, "cli"

    seed_anchor = _max_seed_json_id(seed_data_dir)
    if seed_anchor is not None:
        return seed_anchor, f"seed:{seed_data_dir}"

    raise AnchorResolutionError(
        f"Unable to resolve crawl anchor id. Provide --anchor-id or place numeric *.json files in {seed_data_dir}."
    )


def _resolve_crawl_range(
    anchor_id: int,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
    id_lookahead: int = ID_LOOKAHEAD_DEFAULT,
) -> tuple[int, int]:
    """
    Inclusive id range to crawl around the anchor.

    :param anchor_id: Anchor ticket id.
    :param id_window_back: How many ids before the anchor to re-check (status updates).
    :param id_lookahead: How many ids after the anchor to try (new tickets).
    :raises ValueError: If the anchor is ``None`` or a window is negative.
    :return: ``(start id, end id)``, start at least 1.
    """
    if anchor_id is None:
        raise ValueError("anchor_id must not be None.")
    if id_window_back < 0:
        raise ValueError("id_window_back must be >= 0.")
    if id_lookahead < 0:
        raise ValueError("id_lookahead must be >= 0.")

    start_id = max(1, anchor_id - id_window_back)
    end_id = anchor_id + id_lookahead
    return start_id, end_id


def _extract_item(scraper_output: Any) -> Record | None:
    """
    Return the non-empty ``item`` of a scraper result.

    :param scraper_output: Result of :func:`download_one_id`.
    :return: The item, or ``None`` for failed or empty scrapes.
    """
    if not isinstance(scraper_output, dict):
        return None
    item = scraper_output.get("item")
    if not isinstance(item, dict) or not item:
        return None
    return item


def _scrape_in_chunks(
    start_id: int, end_id: int, known: dict[int, Record], counter: dict[str, int]
) -> Iterator[tuple[int, Any]]:
    """
    Scrape ids ``start_id..end_id`` in parallel, yielding results in id order.

    Ids in ``known`` are not downloaded again. Ids are submitted one chunk at a time, so
    stopping the iteration stops further requests.

    :param start_id: First ticket id.
    :param end_id: Last ticket id (inclusive).
    :param known: Already scraped records by id.
    :param counter: ``counter['requests']`` is increased by the downloads actually started.
    :return: Iterator of ``(ticket id, scraper output)``.
    """
    # Downloads of a chunk that have not started when the iteration stops are cancelled,
    # so they are counted when they run, not when they are submitted.
    counter_lock = threading.Lock()

    def scrape(ticket_id: int) -> Any:
        if ticket_id in known:
            return {"item": known[ticket_id]}
        with counter_lock:
            counter['requests'] += 1
        return download_one_id(ticket_id, source="web")

    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as executor:
        for chunk_start in range(start_id, end_id + 1, SCRAPE_CHUNK_SIZE):
            chunk = range(chunk_start, min(chunk_start + SCRAPE_CHUNK_SIZE, end_id + 1))
            yield from zip(chunk, executor.map(scrape, chunk), strict=True)


def get_plznito_current_data(
    db_max_id: int | None,
    anchor_id: int | None = None,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
    id_lookahead: int = ID_LOOKAHEAD_DEFAULT,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> dict[str, Any]:
    """
    Scrape the web map detail pages of tickets around the anchor id.

    The crawl stops early after :data:`MAX_CONSECUTIVE_SCRAPE_FAILURES` empty results in a row.

    :param db_max_id: Highest ticket id in the DB (the anchor), ``None`` for an empty DB.
    :param anchor_id: Fallback anchor if the DB is empty.
    :param id_window_back: How many ids before the anchor to re-check.
    :param id_lookahead: How many ids after the anchor to try.
    :param seed_data_dir: Last-resort anchor source.
    :return: Update payload ``{"items": [...], "stats": {...}}``.
    """
    resolved_anchor, anchor_source = _resolve_anchor_id(
        db_max_id,
        anchor_id=anchor_id,
        seed_data_dir=seed_data_dir,
    )
    start_id, end_id = _resolve_crawl_range(
        resolved_anchor,
        id_window_back=id_window_back,
        id_lookahead=id_lookahead,
    )

    # Every map page also lists the ~100 most recent tickets: one request usually covers most of the window,
    # and the newest listed id tells how far behind the DB is.
    listed = db.collect_valid_records(scrape_map_page(resolved_anchor), "anchor map page")
    newest_listed = max((record_id for record_id, _ in listed), default=None)
    # Only when the newest ticket is beyond the normal window; otherwise the window already covers it.
    if newest_listed is not None and newest_listed >= end_id:
        end_id = min(newest_listed + id_lookahead, start_id + MAX_CRAWL_IDS)
        logger.warning(
            "DB is %d tickets behind plznito.cz (newest listed id %d, anchor %d); catching up ids %d-%d.",
            newest_listed - resolved_anchor,
            newest_listed,
            resolved_anchor,
            start_id,
            end_id,
        )
    if newest_listed is None:
        logger.warning("Anchor map page for id %d listed no tickets; crawling id by id.", resolved_anchor)
    known = {record_id: record for record_id, record in listed if start_id <= record_id <= end_id}
    logger.info("Anchor map page covers %d ids of the crawl range %d-%d.", len(known), start_id, end_id)

    items: list[Record] = []
    empty_items = 0
    consecutive_failures = 0
    scanned_count = 0
    counter = {'requests': 1}  # the anchor map page
    for ticket_id, scraped_data in _scrape_in_chunks(start_id, end_id, known, counter):
        scanned_count += 1
        if scanned_count % PROGRESS_LOG_EVERY == 0:
            logger.info(
                "Crawl progress: id %d (%d/%d), %d tickets so far.",
                ticket_id,
                scanned_count,
                end_id - start_id + 1,
                len(items),
            )
        item = _extract_item(scraped_data)
        if item is None:
            empty_items += 1
            consecutive_failures += 1
            # Gaps (deleted tickets) below the newest listed id are expected; only stop beyond it.
            beyond_newest = newest_listed is None or ticket_id > newest_listed
            if beyond_newest and consecutive_failures >= MAX_CONSECUTIVE_SCRAPE_FAILURES:
                logger.info(
                    "Stopping crawl after %d consecutive failures at id %d.",
                    consecutive_failures,
                    ticket_id,
                )
                break
            continue
        logger.debug("Downloaded ticket %d via web scraping.", ticket_id)
        items.append(item)
        consecutive_failures = 0

    logger.info(
        "Downloaded current plznito payload via web scraping (anchor=%s, source=%s, range=%d-%d, "
        "scanned=%d, items=%d, empty=%d).",
        resolved_anchor,
        anchor_source,
        start_id,
        end_id,
        scanned_count,
        len(items),
        empty_items,
    )
    stats: dict[str, Any] = {
        'anchor': resolved_anchor,
        'anchor_source': anchor_source,
        'start_id': start_id,
        'end_id': end_id,
        'scanned': scanned_count,
        'newest_listed': newest_listed,
        'requests': counter['requests'],
        'empty': empty_items,
    }
    return {"items": items, "stats": stats}


def _log_update_summary(
    conn: sqlite3.Connection, data_current: dict[str, Any], counts: Counter[str], db_path: str, started: float
) -> None:
    """
    One INFO line describing the run, plus a WARNING when no new tickets seem to arrive any more.

    :param conn: DB connection.
    :param data_current: Scraped payload with ``stats``.
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
        "Update OK in %.1f s: ids %s-%s (anchor %s from %s), %s HTTP requests, %d tickets scraped "
        "(%d new, %d updated, %d unchanged), %s ids without a ticket; %s now has %d records, newest %s.",
        time.monotonic() - started,
        stats.get('start_id'),
        stats.get('end_id'),
        stats.get('anchor'),
        stats.get('anchor_source'),
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
                "the map page format has not changed (python -m plznito_monitoring.scrape_one %d --source web).",
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
    Write a consistent copy of the DB.

    :param db_path: Existing DB file.
    :param backup_path: Target file; its directory must exist.
    """
    with contextlib.closing(db.connect(db_path, create=False)) as conn:
        db.backup(conn, backup_path)
    logger.info("Backed up %s to %s.", db_path, backup_path)


def db_update(
    db_path: str,
    out_dirname: str = "",
    save_update_data: bool = False,
    anchor_id: int | None = None,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
    id_lookahead: int = ID_LOOKAHEAD_DEFAULT,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> None:
    """
    Scrape recent tickets and merge them into the DB in one transaction.

    :param db_path: DB file to update (created if missing).
    :param out_dirname: Directory for the raw snapshot (``save_update_data``).
    :param save_update_data: Also save the raw scraped payload.
    :param anchor_id: Fallback crawl anchor for an empty DB.
    :param id_window_back: How many ids before the anchor to re-check.
    :param id_lookahead: How many ids after the anchor to try.
    :param seed_data_dir: Last-resort anchor source.
    """
    started = time.monotonic()
    with contextlib.closing(db.connect(db_path)) as conn, _recorded_run(conn, "update") as stats:
        data_current = get_plznito_current_data(
            db.max_id(conn),
            anchor_id=anchor_id,
            id_window_back=id_window_back,
            id_lookahead=id_lookahead,
            seed_data_dir=seed_data_dir,
        )
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
    mode.add_argument("--restore", action="store_true", help="Merge the snapshots in --seed-data-dir, no scraping.")
    mode.add_argument("--import-json", type=str, default=None, help="Merge a JSON list of records, no scraping.")
    mode.add_argument("--export-json", type=str, default=None, help="Write all records as a JSON list.")
    mode.add_argument("--backup", type=str, default=None, help="Write a copy of the DB to this file.")
    parser.add_argument("--save_update_data", action="store_true")
    parser.add_argument("--anchor-id", type=int, default=None)
    parser.add_argument("--id-window-back", type=int, default=ID_WINDOW_BACK_DEFAULT)
    parser.add_argument("--id-lookahead", type=int, default=ID_LOOKAHEAD_DEFAULT)
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
            anchor_id=args.anchor_id,
            id_window_back=args.id_window_back,
            id_lookahead=args.id_lookahead,
            seed_data_dir=args.seed_data_dir,
        )


if __name__ == "__main__":
    main()

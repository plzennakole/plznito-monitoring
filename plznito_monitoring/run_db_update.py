# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Update the Plznito JSON DB by scraping recent tickets, or rebuild it from snapshots (``--restore``)."""

import argparse
import bz2
import json
import logging
import os
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any

from plznito_monitoring.common import atomic_write_json, load_json, parse_int, setup_logging
from plznito_monitoring.cyklo_filter import filter_cyklo_items
from plznito_monitoring.restore_all import download_one_id, scrape_map_page
from plznito_monitoring.run_map_render import _parse_item_datetime

logger = logging.getLogger(__name__)
# Defaults live next to this file, so the scripts work from any working directory.
PLZNITO_DIR = Path(__file__).parent
CYKLO_DB_FILENAME = "plznito_cyklo.json"
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
    """Raised when a DB file or snapshot does not have the expected JSON shape."""


class AnchorResolutionError(ValueError):
    """Raised when no ticket id is known to start the crawl from."""


def _validate_payload(data: Any) -> dict[str, list[Any]]:
    """
    Check that an update payload is ``{"items": [...]}``.

    :param data: Parsed payload.
    :raises InvalidPayloadError: If the shape is different.
    :return: The payload.
    """
    if not isinstance(data, dict):
        raise InvalidPayloadError("Expected payload to be a JSON object.")
    if "items" not in data or not isinstance(data["items"], list):
        raise InvalidPayloadError("Expected payload to contain 'items' list.")
    return data


def _collect_valid_records(records: list[Any], source_name: str) -> list[tuple[int, Record]]:
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

        normalized_id = parse_int(record.get("id"))
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
    existing_records: list[Any],
    anchor_id: int | None = None,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> tuple[int, str]:
    """
    Pick the ticket id to crawl around: the DB's highest id, else the CLI value, else the seed directory.

    :param existing_records: Records already in the DB.
    :param anchor_id: Anchor given on the command line.
    :param seed_data_dir: Directory with per-id seed files.
    :raises AnchorResolutionError: If none of the sources gives an id.
    :return: ``(anchor id, source name)``.
    """
    db_anchor = max(
        (record_id for record_id, _ in _collect_valid_records(existing_records, "existing DB")), default=None
    )
    if db_anchor is not None:
        return db_anchor, "db"

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
    :param counter: ``counter['requests']`` is increased by the downloads actually submitted.
    :return: Iterator of ``(ticket id, scraper output)``.
    """

    def scrape(ticket_id: int) -> Any:
        if ticket_id in known:
            return {"item": known[ticket_id]}
        return download_one_id(ticket_id, source="web")

    with ThreadPoolExecutor(max_workers=SCRAPE_WORKERS) as executor:
        for chunk_start in range(start_id, end_id + 1, SCRAPE_CHUNK_SIZE):
            chunk = range(chunk_start, min(chunk_start + SCRAPE_CHUNK_SIZE, end_id + 1))
            counter['requests'] += sum(1 for ticket_id in chunk if ticket_id not in known)
            yield from zip(chunk, executor.map(scrape, chunk), strict=True)


def get_plznito_current_data(
    existing_records: list[Any],
    anchor_id: int | None = None,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
    id_lookahead: int = ID_LOOKAHEAD_DEFAULT,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> dict[str, Any]:
    """
    Scrape the web map detail pages of tickets around the anchor id.

    The crawl stops early after :data:`MAX_CONSECUTIVE_SCRAPE_FAILURES` empty results in a row.

    :param existing_records: Records already in the DB (used to find the anchor).
    :param anchor_id: Fallback anchor if the DB is empty.
    :param id_window_back: How many ids before the anchor to re-check.
    :param id_lookahead: How many ids after the anchor to try.
    :param seed_data_dir: Last-resort anchor source.
    :return: Update payload ``{"items": [...], "stats": {...}}``.
    """
    resolved_anchor, anchor_source = _resolve_anchor_id(
        existing_records,
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
    listed = _collect_valid_records(scrape_map_page(resolved_anchor), "anchor map page")
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


def _newest_ticket(records: list[Any]) -> tuple[int, datetime | None] | None:
    """
    Id and date of the newest ticket.

    :param records: DB records.
    :return: ``(id, date)``, or ``None`` for an empty DB.
    """
    valid = _collect_valid_records(records, "updated DB")
    if not valid:
        return None
    newest_id, newest_record = max(valid, key=lambda entry: entry[0])
    return newest_id, _parse_item_datetime(newest_record)[0]


def _log_update_summary(
    data_db: list[Any], data_current: dict[str, Any], data_out: list[Any], db_path: str, started: float
) -> None:
    """
    One INFO line describing the run, plus a WARNING when no new tickets seem to arrive any more.

    :param data_db: Records before the update.
    :param data_current: Scraped payload with ``stats``.
    :param data_out: Records written to the DB.
    :param db_path: DB file path.
    :param started: ``time.monotonic()`` at the start of the run.
    """
    old_ids = {record_id for record_id, _ in _collect_valid_records(data_db, "existing DB")}
    scraped_ids = [record_id for record_id, _ in _collect_valid_records(data_current["items"], "incoming update")]
    new_count = sum(1 for record_id in scraped_ids if record_id not in old_ids)
    stats = data_current.get("stats", {})
    newest = _newest_ticket(data_out)
    newest_text = "none"
    if newest is not None:
        newest_text = f"#{newest[0]}" + (f" from {newest[1]:%d.%m.%Y}" if newest[1] else "")
    logger.info(
        "Update OK in %.1f s: ids %s-%s (anchor %s from %s), %s HTTP requests, %d tickets scraped "
        "(%d new, %d updated), %s ids without a ticket; %s now has %d records, newest %s.",
        time.monotonic() - started,
        stats.get('start_id'),
        stats.get('end_id'),
        stats.get('anchor'),
        stats.get('anchor_source'),
        stats.get('requests'),
        len(scraped_ids),
        new_count,
        len(scraped_ids) - new_count,
        stats.get('empty'),
        db_path,
        len(data_out),
        newest_text,
    )
    if new_count == 0 and newest is not None and newest[1] is not None:
        age_days = (datetime.now() - newest[1]).days
        if age_days > STALE_AFTER_DAYS:
            logger.warning(
                "No new tickets: the newest ticket #%d is %d days old. Check that plznito.cz is reachable and "
                "the map page format has not changed (python -m plznito_monitoring.scrape_one %d --source web).",
                newest[0],
                age_days,
                newest[0] + 1,
            )


def _load_record_list(path: str | None, missing_ok_label: str) -> list[Any]:
    """
    Load a JSON list of records; a missing (or unset) file gives an empty list.

    :param path: File path, or ``None``.
    :param missing_ok_label: Label used in the warning for a missing file.
    :raises InvalidPayloadError: If the file does not contain a JSON list.
    :return: Records.
    """
    if path is None:
        return []
    if not os.path.exists(path):
        logger.warning("%s %s does not exist. Starting with empty list.", missing_ok_label, path)
        return []
    data = load_json(path)
    if not isinstance(data, list):
        raise InvalidPayloadError(f"{missing_ok_label} {path} must contain a JSON list.")
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


def filter_data(data: dict[str, list[Any]]) -> list[Record]:
    """
    Keep only the cycling-related items of an update payload.

    :param data: Update payload ``{"items": [...]}``.
    :raises InvalidPayloadError: If the payload has a different shape.
    :return: Cycling items.
    """
    items = _validate_payload(data)["items"]
    data_cyklo = filter_cyklo_items(items)
    logger.info("Filtered cycling items: %d/%d.", len(data_cyklo), len(items))
    return data_cyklo


def merge_data(data_old: list[Any], data_new: list[Any]) -> list[Record]:
    """
    Merge new records into old ones by id.

    New non-empty fields overwrite old ones, other old fields are kept, so a lightweight
    scraped record does not wipe ``created``, ``category_id`` etc. of a full API record.
    Updated and new records move to the end, later duplicates win.

    :param data_old: Records in the DB.
    :param data_new: Incoming records.
    :return: Merged records.
    """
    valid_old = _collect_valid_records(data_old, "existing DB")
    valid_new = _collect_valid_records(data_new, "incoming update")

    merged = dict(valid_old)
    for record_id, record in valid_new:
        old_record = merged.pop(record_id, {})
        new_fields = {key: value for key, value in record.items() if value not in (None, "", [], {})}
        merged[record_id] = {**old_record, **new_fields}
    data_out = list(merged.values())

    logger.info(
        "Merging old=%d valid/%d input + new=%d valid/%d input => %d output.",
        len(valid_old),
        len(data_old),
        len(valid_new),
        len(data_new),
        len(data_out),
    )
    return data_out


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


def db_restore(
    out_json_name: str,
    start_json_name: str | None = None,
    data_dirname: str = ".",
    filter_cyklo: bool = True,
) -> None:
    """
    Rebuild the DB from a seed DB file plus all snapshots in ``data_dirname``.

    :param out_json_name: Output DB path.
    :param start_json_name: Optional existing DB to start from.
    :param data_dirname: Directory with ``.json`` / ``.json.bz2`` snapshots, applied in name order.
    :param filter_cyklo: Keep only cycling-related records.
    """
    data = _load_record_list(start_json_name, "Restore seed file")

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

    if filter_cyklo:
        items = filter_cyklo_items(items)
    data = merge_data(data, items)

    atomic_write_json(out_json_name, data)
    logger.info("Restore completed: wrote %d items to %s.", len(data), out_json_name)


def db_update(
    json_db_file_path: str,
    out_dirname: str = "",
    filter_cyklo: bool = True,
    save_update_data: bool = False,
    write_cyklo_json_path: str | None = None,
    anchor_id: int | None = None,
    id_window_back: int = ID_WINDOW_BACK_DEFAULT,
    id_lookahead: int = ID_LOOKAHEAD_DEFAULT,
    seed_data_dir: str = SEED_DATA_DIR_DEFAULT,
) -> None:
    """
    Scrape recent tickets and merge them into the JSON DB.

    :param json_db_file_path: DB file to update (created if missing).
    :param out_dirname: Directory for the raw snapshot (``save_update_data``).
    :param filter_cyklo: Store only cycling-related tickets in the DB.
    :param save_update_data: Also save the raw scraped payload.
    :param write_cyklo_json_path: Also write the cycling subset to this path.
    :param anchor_id: Fallback crawl anchor for an empty DB.
    :param id_window_back: How many ids before the anchor to re-check.
    :param id_lookahead: How many ids after the anchor to try.
    :param seed_data_dir: Last-resort anchor source.
    """
    started = time.monotonic()
    data_db = _load_record_list(json_db_file_path, "DB file")

    data_current = get_plznito_current_data(
        data_db,
        anchor_id=anchor_id,
        id_window_back=id_window_back,
        id_lookahead=id_lookahead,
        seed_data_dir=seed_data_dir,
    )

    if save_update_data:
        _save_raw_snapshot(data_current, out_dirname)

    if filter_cyklo:
        data_cyklo_updated = merge_data(data_db, filter_data(data_current))
        atomic_write_json(json_db_file_path, data_cyklo_updated)
        data_out = data_cyklo_updated
        if write_cyklo_json_path:
            atomic_write_json(write_cyklo_json_path, data_cyklo_updated)
            logger.info("Wrote derived cycling DB to %s.", write_cyklo_json_path)
    else:
        data_updated = merge_data(data_db, data_current["items"])
        atomic_write_json(json_db_file_path, data_updated)
        data_out = data_updated
        if write_cyklo_json_path:
            data_cyklo_updated = filter_cyklo_items(data_updated)
            atomic_write_json(write_cyklo_json_path, data_cyklo_updated)
            logger.info(
                "Wrote derived cycling DB to %s (%d items).",
                write_cyklo_json_path,
                len(data_cyklo_updated),
            )

    _log_update_summary(data_db, data_current, data_out, json_db_file_path, started)


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--db_json", type=str, default=str(PLZNITO_DIR / CYKLO_DB_FILENAME))
    parser.add_argument("--filter_cyklo", dest="filter_cyklo", action="store_true")
    parser.add_argument("--no-filter-cyklo", dest="filter_cyklo", action="store_false")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--save_update_data", action="store_true")
    parser.add_argument("--write-cyklo-json", type=str, default=None)
    parser.add_argument("--anchor-id", type=int, default=None)
    parser.add_argument("--id-window-back", type=int, default=ID_WINDOW_BACK_DEFAULT)
    parser.add_argument("--id-lookahead", type=int, default=ID_LOOKAHEAD_DEFAULT)
    parser.add_argument("--seed-data-dir", type=str, default=SEED_DATA_DIR_DEFAULT)
    parser.set_defaults(filter_cyklo=None)
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
    Run the update or restore selected by the command line.

    :param args: Parsed arguments.
    """
    if args.filter_cyklo is None:
        filter_cyklo = os.path.basename(args.db_json) == CYKLO_DB_FILENAME
    else:
        filter_cyklo = args.filter_cyklo

    if args.restore:
        restore_seed = args.db_json if os.path.exists(args.db_json) else None
        db_restore(
            args.db_json, start_json_name=restore_seed, data_dirname=args.seed_data_dir, filter_cyklo=filter_cyklo
        )
        return

    if args.save_update_data:
        os.makedirs(SNAPSHOT_DIR_DEFAULT, exist_ok=True)
    db_update(
        args.db_json,
        out_dirname=SNAPSHOT_DIR_DEFAULT,
        filter_cyklo=filter_cyklo,
        save_update_data=args.save_update_data,
        write_cyklo_json_path=args.write_cyklo_json,
        anchor_id=args.anchor_id,
        id_window_back=args.id_window_back,
        id_lookahead=args.id_lookahead,
        seed_data_dir=args.seed_data_dir,
    )


if __name__ == "__main__":
    main()

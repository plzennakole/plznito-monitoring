import argparse
import bz2
import json
import logging
import os
import tempfile
from datetime import datetime

from cyklo_filter import filter_cyklo_items, to_lower_text
from restore_all import download_one_id
SCRAPER_IMPORT_ERROR = None

logger = logging.getLogger(__name__)
CYKLO_DB_FILENAME = "plznito_cyklo.json"
ID_WINDOW_BACK_DEFAULT = 100
ID_LOOKAHEAD_DEFAULT = 200
SEED_DATA_DIR_DEFAULT = "data"
MAX_CONSECUTIVE_SCRAPE_FAILURES = 10


def _load_json_file(file_path):
    with open(file_path, encoding="utf-8") as fr:
        return json.load(fr)


def _atomic_write_json(file_path, data, indent=4):
    target_dir = os.path.dirname(os.path.abspath(file_path))
    os.makedirs(target_dir, exist_ok=True)

    temp_path = None
    fd, temp_path = tempfile.mkstemp(prefix=".tmp_json_", suffix=".json", dir=target_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fw:
            json.dump(data, fw, indent=indent)
        os.replace(temp_path, file_path)
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)


def _validate_payload(data):
    if not isinstance(data, dict):
        raise ValueError("Expected payload to be a JSON object.")
    if "items" not in data or not isinstance(data["items"], list):
        raise ValueError("Expected payload to contain 'items' list.")
    return data


def _normalize_id(record_id):
    if isinstance(record_id, bool):
        return None
    if isinstance(record_id, int):
        return record_id
    if isinstance(record_id, str):
        stripped = record_id.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def _collect_valid_records(records, source_name):
    valid_records = []
    skipped_invalid = 0

    for record in records:
        if not isinstance(record, dict):
            skipped_invalid += 1
            logger.debug("Skipping non-dict record from %s: %r", source_name, record)
            continue

        normalized_id = _normalize_id(record.get("id"))
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


def _save_raw_snapshot(data, out_dirname):
    target_dir = out_dirname or "."
    os.makedirs(target_dir, exist_ok=True)
    snapshot_filename = datetime.today().strftime("%Y-%m-%d-%H:%M:%S") + ".json.bz2"
    snapshot_path = os.path.join(target_dir, snapshot_filename)
    with bz2.open(snapshot_path, "wt", encoding="utf-8") as f:
        json.dump(data, f, indent=4)
    logger.info("Saved raw update snapshot to %s.", snapshot_path)


def _max_record_id(records):
    max_id = None
    for record in records:
        if not isinstance(record, dict):
            continue
        normalized_id = _normalize_id(record.get("id"))
        if normalized_id is None:
            continue
        if max_id is None or normalized_id > max_id:
            max_id = normalized_id
    return max_id


def _max_seed_json_id(seed_data_dir):
    if not seed_data_dir:
        return None
    if not os.path.isdir(seed_data_dir):
        logger.warning("Seed data directory %s does not exist.", seed_data_dir)
        return None

    max_seed_id = None
    for fname in os.listdir(seed_data_dir):
        if not fname.endswith(".json"):
            continue
        fname_base = os.path.splitext(fname)[0]
        if not fname_base.isdigit():
            continue
        file_id = int(fname_base)
        if max_seed_id is None or file_id > max_seed_id:
            max_seed_id = file_id
    return max_seed_id


def _resolve_anchor_id(existing_records, anchor_id=None, seed_data_dir=SEED_DATA_DIR_DEFAULT):
    db_anchor = _max_record_id(existing_records)
    if db_anchor is not None:
        return db_anchor, "db"

    cli_anchor = _normalize_id(anchor_id)
    if cli_anchor is not None:
        return cli_anchor, "cli"

    seed_anchor = _max_seed_json_id(seed_data_dir)
    if seed_anchor is not None:
        return seed_anchor, f"seed:{seed_data_dir}"

    raise ValueError(
        "Unable to resolve crawl anchor id. Provide --anchor-id or place numeric *.json files "
        f"in {seed_data_dir}."
    )


def _resolve_crawl_range(anchor_id, id_window_back=ID_WINDOW_BACK_DEFAULT, id_lookahead=ID_LOOKAHEAD_DEFAULT):
    if anchor_id is None:
        raise ValueError("anchor_id must not be None.")
    if id_window_back < 0:
        raise ValueError("id_window_back must be >= 0.")
    if id_lookahead < 0:
        raise ValueError("id_lookahead must be >= 0.")

    start_id = max(1, anchor_id - id_window_back)
    end_id = anchor_id + id_lookahead
    return start_id, end_id


def _extract_item(scraper_output):
    if not isinstance(scraper_output, dict):
        return None
    item = scraper_output.get("item")
    if not isinstance(item, dict) or not item:
        return None
    return item


def get_plznito_current_data(
    existing_records,
    anchor_id=None,
    id_window_back=ID_WINDOW_BACK_DEFAULT,
    id_lookahead=ID_LOOKAHEAD_DEFAULT,
    seed_data_dir=SEED_DATA_DIR_DEFAULT,
):
    """
    Build current update payload by scraping web map ticket detail pages.
    """
    if download_one_id is None:  # pragma: no cover - environment dependent
        raise RuntimeError(f"Web scraper unavailable: {SCRAPER_IMPORT_ERROR}")

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

    items = []
    empty_items = 0
    consecutive_failures = 0
    scanned_count = 0
    for ticket_id in range(start_id, end_id + 1):
        scanned_count += 1
        try:
            scraped_data = download_one_id(ticket_id, source="web")
            logger.info("Downloaded ticket %d via web scraping.", ticket_id)
        except Exception as exc:  # pragma: no cover - network/runtime dependent
            logger.warning("Web scrape failed for id %s: %s", ticket_id, exc)
            empty_items += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_SCRAPE_FAILURES:
                logger.warning(
                    "Stopping crawl after %d consecutive failures at id %d.",
                    consecutive_failures,
                    ticket_id,
                )
                break
            continue

        item = _extract_item(scraped_data)
        if item is None:
            empty_items += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_SCRAPE_FAILURES:
                logger.warning(
                    "Stopping crawl after %d consecutive failures at id %d.",
                    consecutive_failures,
                    ticket_id,
                )
                break
            continue
        items.append(item)
        consecutive_failures = 0

    payload = _validate_payload({"items": items})
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
    return payload


def _load_db_records(json_db_file_path):
    if not os.path.exists(json_db_file_path):
        logger.warning("DB file %s does not exist. Initializing with empty list.", json_db_file_path)
        return []

    data_db = _load_json_file(json_db_file_path)
    if not isinstance(data_db, list):
        raise ValueError(f"DB file {json_db_file_path} must contain a JSON list.")
    return data_db


def _load_snapshot_payload(full_fname):
    if full_fname.endswith(".json"):
        return _load_json_file(full_fname)
    if full_fname.endswith(".bz2"):
        with bz2.open(full_fname, "rt", encoding="utf-8") as fr:
            return json.load(fr)
    return None


def _restore_seed_data(start_json_name):
    if start_json_name is None:
        return []
    if not os.path.exists(start_json_name):
        logger.warning("Restore seed file %s does not exist. Starting with empty DB.", start_json_name)
        return []
    data = _load_json_file(start_json_name)
    if not isinstance(data, list):
        raise ValueError(f"Restore seed file {start_json_name} must contain a JSON list.")
    return data


def filter_data(data):
    """
    Simple filtering of cycling items
    """
    payload = _validate_payload(data)
    items = payload["items"]
    data_cyklo = filter_cyklo_items(items)
    skipped_missing_text = 0
    for item in items:
        if not isinstance(item, dict):
            skipped_missing_text += 1
            logger.debug("Skipping non-dict item in filter_data: %r", item)
            continue

        report_text = to_lower_text(item.get("report"))
        name_text = to_lower_text(item.get("name"))
        if not report_text and not name_text:
            skipped_missing_text += 1
            logger.debug("Skipping item without report/name text. id=%s", item.get("id"))

    logger.info(
        "Filtered cycling items: %d/%d (skipped_missing_text=%d).",
        len(data_cyklo),
        len(items),
        skipped_missing_text,
    )
    return data_cyklo


def merge_data(data_old, data_new):
    """
    Merge two json, use the newer data
    """
    valid_old = _collect_valid_records(data_old, "existing DB")
    valid_new = _collect_valid_records(data_new, "incoming update")

    ids_new = {record_id for record_id, _ in valid_new}
    data_old_filtered = [record for record_id, record in valid_old if record_id not in ids_new]
    data_out = data_old_filtered + [record for _, record in valid_new]

    logger.info(
        "Merging old=%d valid/%d input + new=%d valid/%d input => %d output.",
        len(valid_old),
        len(data_old),
        len(valid_new),
        len(data_new),
        len(data_out),
    )
    return data_out


def db_restore(start_json_name=None, data_dirname="."):
    # how to process all data
    # aka DB restore
    data = _restore_seed_data(start_json_name)

    for fname in sorted(os.listdir(data_dirname)):
        full_fname = os.path.join(data_dirname, fname)
        if not (full_fname.endswith(".json") or full_fname.endswith(".bz2")):
            continue

        try:
            data_new = _load_snapshot_payload(full_fname)
            if data_new is None:
                continue
            data_cyklo = filter_data(data_new)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skipping snapshot %s due to invalid payload: %s", full_fname, exc)
            continue

        data = merge_data(data, data_cyklo)

    _atomic_write_json("plznito_cyklo.json", data, indent=4)
    logger.info("Restore completed: wrote %d items to plznito_cyklo.json.", len(data))


def db_update(
    json_db_file_path,
    out_dirname="",
    filter_cyklo=True,
    save_update_data=False,
    write_cyklo_json_path=None,
    anchor_id=None,
    id_window_back=ID_WINDOW_BACK_DEFAULT,
    id_lookahead=ID_LOOKAHEAD_DEFAULT,
    seed_data_dir=SEED_DATA_DIR_DEFAULT,
):
    """
    update with daily data
    """
    # add data to our db
    data_db = _load_db_records(json_db_file_path)

    # load new data
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
        data_cyklo_current = filter_data(data_current)
        data_cyklo_updated = merge_data(data_db, data_cyklo_current)
        _atomic_write_json(json_db_file_path, data_cyklo_updated, indent=4)
        if write_cyklo_json_path:
            _atomic_write_json(write_cyklo_json_path, data_cyklo_updated, indent=4)
            logger.info("Wrote derived cycling DB to %s.", write_cyklo_json_path)
    else:
        data_updated = merge_data(data_db, data_current["items"])
        _atomic_write_json(json_db_file_path, data_updated, indent=4)
        if write_cyklo_json_path:
            data_cyklo_updated = filter_cyklo_items(data_updated)
            _atomic_write_json(write_cyklo_json_path, data_cyklo_updated, indent=4)
            logger.info(
                "Wrote derived cycling DB to %s (%d items).",
                write_cyklo_json_path,
                len(data_cyklo_updated),
            )

    logger.info("Merging finished. Output file: %s", json_db_file_path)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--db_json", type=str, default=CYKLO_DB_FILENAME)
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

    logging.basicConfig(filename='plznito_monitoring.log',
                        level=logging.INFO,
                        format='%(asctime)s %(message)s')

    logger.info(f"Running with args: {args}")

    if args.restore:
        restore_seed = args.db_json if os.path.exists(args.db_json) else None
        db_restore(start_json_name=restore_seed, data_dirname="data")
        raise SystemExit(0)

    if args.filter_cyklo is None:
        filter_cyklo = os.path.basename(args.db_json) == CYKLO_DB_FILENAME
    else:
        filter_cyklo = args.filter_cyklo

    db_update(
        args.db_json,
        out_dirname="notebooks",
        filter_cyklo=filter_cyklo,
        save_update_data=args.save_update_data,
        write_cyklo_json_path=args.write_cyklo_json,
        anchor_id=args.anchor_id,
        id_window_back=args.id_window_back,
        id_lookahead=args.id_lookahead,
        seed_data_dir=args.seed_data_dir,
    )

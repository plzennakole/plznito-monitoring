import argparse
import bz2
import json
import logging
import os
import tempfile
import time
from datetime import datetime

import requests
from cyklo_filter import filter_cyklo_items, to_lower_text
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)
CYKLO_DB_FILENAME = "plznito_cyklo.json"
PLZNITO_LIST_URL = "http://plzni.to/api/1.0/tickets/list?categoryId=0&statusId=0&arch=0&term=&own=0&term="
REQUEST_TIMEOUT_SECONDS = 30
REQUEST_RETRIES = 3
REQUEST_BACKOFF_SECONDS = 1.0


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


def _request_with_retries(url, timeout=REQUEST_TIMEOUT_SECONDS, retries=REQUEST_RETRIES):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            return response
        except RequestException as exc:
            last_exc = exc
            if attempt == retries:
                break
            sleep_for = REQUEST_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Request failed (attempt %d/%d): %s. Retrying in %.1fs.",
                attempt,
                retries,
                exc,
                sleep_for,
            )
            time.sleep(sleep_for)
    raise last_exc


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


def get_plznito_current_data():
    """
    Download json with all plzni.to data
    """
    response = _request_with_retries(PLZNITO_LIST_URL)
    data = response.json()
    payload = _validate_payload(data)
    logger.info("Downloaded current plznito json (%d items).", len(payload["items"]))
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


def db_update(json_db_file_path, out_dirname="", filter_cyklo=True, save_update_data=False):
    """
    update with daily data
    """
    # load new data
    data_current = get_plznito_current_data()

    if save_update_data:
        _save_raw_snapshot(data_current, out_dirname)

    # add data to our db
    data_db = _load_db_records(json_db_file_path)

    if filter_cyklo:
        data_cyklo_current = filter_data(data_current)
        data_cyklo_updated = merge_data(data_db, data_cyklo_current)
        _atomic_write_json(json_db_file_path, data_cyklo_updated, indent=4)
    else:
        data_updated = merge_data(data_db, data_current["items"])
        _atomic_write_json(json_db_file_path, data_updated, indent=4)

    logger.info("Merging finished. Output file: %s", json_db_file_path)


if __name__ == "__main__":

    parser = argparse.ArgumentParser()
    parser.add_argument("--db_json", type=str, default=CYKLO_DB_FILENAME)
    parser.add_argument("--filter_cyklo", dest="filter_cyklo", action="store_true")
    parser.add_argument("--no-filter-cyklo", dest="filter_cyklo", action="store_false")
    parser.add_argument("--restore", action="store_true")
    parser.add_argument("--save_update_data", action="store_true")
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
    )

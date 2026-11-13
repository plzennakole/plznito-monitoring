# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""
Download Plznito tickets from the plznito.cz JSON API (v3, the one the web app uses).

Run as a script, it bulk-downloads a range of ticket ids into per-id seed files
(``data/<id>.json``); rebuild the DB from them with ``run_db_update --restore``.
"""

import argparse
import json
import logging
import os
from typing import Any

import requests
import tqdm
from requests.adapters import HTTPAdapter
from requests.exceptions import RequestException
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

API_BASE_URL = "https://www.plznito.cz/api/3.0"
API_DETAIL_URL = API_BASE_URL + "/ticket/detail/{ticket_id}"
API_LIST_URL = API_BASE_URL + "/tickets/list"
REQUEST_TIMEOUT_SECONDS = 20
USER_AGENT = "plznito-monitoring/1.0 (+https://github.com/plzennakole/plznito-monitoring)"
# The detail endpoint answers 503 (not 404) for deleted and archived tickets.
MISSING_TICKET_STATUSES = (404, 503)
# Fields describing what the current viewer may do with the ticket, not the ticket itself.
VIEWER_FIELDS = ('urgency', 'editable')

Record = dict[str, Any]


class PlznitoApiError(Exception):
    """Raised when the plznito.cz API answers with an unexpected payload."""


def _make_session() -> requests.Session:
    """
    HTTP session reusing connections, retrying transient errors with backoff (1 s, 2 s, 4 s).

    503 is not retried: the API uses it for missing tickets, so retrying would only slow down gaps in ids.

    :return: Configured session.
    """
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 504],
        allowed_methods=['GET'],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
    session.mount('https://', adapter)
    session.mount('http://', adapter)
    session.headers['User-Agent'] = USER_AGENT
    return session


# Shared by all downloads; requests sessions are safe for concurrent GETs.
SESSION = _make_session()


def normalize_api_item(item: Record) -> Record:
    """
    Turn an API ticket into the record stored in the DB.

    Drops the per-viewer fields and adds ``report`` (the cycling filter reads it; the old API had it,
    v3 only has ``description``).

    :param item: Ticket from the API.
    :return: New record dict.
    """
    record = {key: value for key, value in item.items() if key not in VIEWER_FIELDS}
    if not record.get('report'):
        record['report'] = record.get('description') or ""
    return record


def download_one_id(ticket_id: int) -> dict[str, Any]:
    """
    Download one ticket.

    :param ticket_id: Plznito ticket id.
    :return: ``{"item": {...}}``, or ``{}`` if the ticket does not exist or could not be fetched.
    """
    try:
        r = SESSION.get(API_DETAIL_URL.format(ticket_id=ticket_id), timeout=REQUEST_TIMEOUT_SECONDS)
        if r.status_code in MISSING_TICKET_STATUSES:
            # Expected for deleted and archived tickets; the crawl summary counts them.
            logger.debug("No ticket %s (HTTP %d).", ticket_id, r.status_code)
            return {}
        r.raise_for_status()
        payload = r.json()
    except (RequestException, ValueError) as e:
        logger.warning("Download failed for id %s: %s", ticket_id, e)
        return {}

    item = payload.get('item') if isinstance(payload, dict) else None
    if not isinstance(item, dict) or not item:
        logger.warning("Unexpected detail payload for id %s: %.200r", ticket_id, payload)
        return {}
    return {"item": normalize_api_item(item)}


def download_recent(recent_days: int) -> list[Record]:
    """
    Download the list of recent tickets (the API returns at most the newest ~500).

    :param recent_days: Tickets created in the last N days.
    :raises requests.RequestException: If the request fails.
    :raises PlznitoApiError: If the response is not ``{"items": [...]}``.
    :return: Ticket records.
    """
    r = SESSION.get(API_LIST_URL, params={'recentDays': recent_days}, timeout=REQUEST_TIMEOUT_SECONDS)
    r.raise_for_status()
    payload = r.json()
    items = payload.get('items') if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise PlznitoApiError(f"Unexpected ticket list payload from {API_LIST_URL}: {str(payload)[:200]}")
    return [normalize_api_item(item) for item in items if isinstance(item, dict)]


def download_range(data_dir: str, start_id: int, end_id: int) -> None:
    """
    Download tickets ``start_id..end_id`` (inclusive) into ``<data_dir>/<id>.json``.

    Ids that already have a file are skipped (delete a file to download it again);
    failed downloads are not written.

    :param data_dir: Existing target directory.
    :param start_id: First ticket id.
    :param end_id: Last ticket id.
    """
    # One directory listing instead of an existence check per id.
    existing = set(os.listdir(data_dir))
    for ticket_id in tqdm.tqdm(range(start_id, end_id + 1)):
        fname = f"{ticket_id}.json"
        if fname in existing:
            continue
        json_data = download_one_id(ticket_id)
        if not json_data:
            continue
        with open(os.path.join(data_dir, fname), "w", encoding="utf-8") as f:
            json.dump(json_data, f)


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", type=str, default=os.path.join(os.path.dirname(__file__), "data"))
    parser.add_argument("--start-id", type=int, required=True)
    parser.add_argument("--end-id", type=int, required=True)
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    download_range(args.data_dir, args.start_id, args.end_id)


if __name__ == "__main__":
    main()

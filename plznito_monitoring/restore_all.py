# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""
Download single Plznito tickets (JSON API or web map page).

Run as a script, it bulk-downloads a range of ticket ids into per-id seed files
(``data/<id>.json``); rebuild the DB from them with ``run_db_update --restore``.
"""

import argparse
import json
import logging
import os
import re
from typing import Any

import json5
import requests
import tqdm
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

API_DETAIL_URL = "https://www.plznito.cz/api/1.0/tickets/detail/{ticket_id}"
MAP_URL = "https://www.plznito.cz/map/{ticket_id}"
REQUEST_TIMEOUT_SECONDS = 20


def download_one_id(ticket_id: int, source: str = "auto") -> dict[str, Any]:
    """
    Download one ticket.

    :param ticket_id: Plznito ticket id.
    :param source: ``"api"`` (JSON API only), ``"web"`` (map page only) or ``"auto"`` (API, then map page).
    :return: ``{"item": {...}}`` (plus extra keys from the API), or ``{}`` if the ticket could not be fetched.
    """
    if source in ("auto", "api"):
        try:
            r = requests.get(API_DETAIL_URL.format(ticket_id=ticket_id), timeout=REQUEST_TIMEOUT_SECONDS)
            r.raise_for_status()
            out: dict[str, Any] = r.json()
            if out:
                return out
        except (RequestException, ValueError) as e:
            logger.warning("API failed for id %s: %s", ticket_id, e)
        if source == "api":
            return {}
    return scrape_one_id_from_map(ticket_id)


def scrape_one_id_from_map(ticket_id: int) -> dict[str, Any]:
    """
    Scrape one ticket from its web map page.

    :param ticket_id: Plznito ticket id.
    :return: ``{"item": {...}}``, or ``{}`` if the page failed to load or does not contain the ticket.
    """
    try:
        r = requests.get(MAP_URL.format(ticket_id=ticket_id), timeout=REQUEST_TIMEOUT_SECONDS)
        r.raise_for_status()
    except RequestException as e:
        logger.warning("Map fetch failed for id %s: %s", ticket_id, e)
        return {}

    try:
        locations = _parse_locations_from_html(r.text)
    except ValueError as e:
        logger.warning("Failed to parse locations for id %s: %s", ticket_id, e)
        return {}

    for location in locations:
        if str(location.get("id")) == str(ticket_id):
            return {"item": _location_to_item(location)}

    logger.warning("No matching item for id %s in map page", ticket_id)
    return {}


def _parse_locations_from_html(html: str) -> list[dict[str, Any]]:
    """
    Extract the ``var locations = [...]`` JS array from a map page.

    :param html: Page HTML.
    :raises ValueError: If the array is not valid JSON5.
    :return: Location objects, empty if the page has no such array.
    """
    match = re.search(r"var\s+locations\s*=\s*(\[[\s\S]*?\])\s*;", html)
    if not match:
        return []
    locations: list[dict[str, Any]] = json5.loads(match.group(1))
    return locations


def _location_to_item(location: dict[str, Any]) -> dict[str, Any]:
    """
    Map a map-page location to the record fields used by the rest of the pipeline.

    :param location: One element of the page's ``locations`` array.
    :return: Lightweight ticket record (``report`` duplicates ``description`` for the cycling filter).
    """
    return {
        "id": location.get("id"),
        "name": location.get("name", ""),
        "report": location.get("description", ""),
        "description": location.get("description", ""),
        "solution": location.get("solution"),
        "latitude": str(location.get("lat", "")) if "lat" in location else "",
        "longitude": str(location.get("lng", "")) if "lng" in location else "",
        "status": location.get("status"),
        "status_id": location.get("status_id"),
        "category": location.get("category"),
        "address": location.get("address", ""),
        "photos": location.get("photos", []),
        "date": location.get("date"),
    }


def download_range(data_dir: str, start_id: int, end_id: int, source: str) -> None:
    """
    Download tickets ``start_id..end_id`` (inclusive) into ``<data_dir>/<id>.json``.

    Ids that already have a file are skipped (delete a file to download it again);
    failed downloads are not written.

    :param data_dir: Existing target directory.
    :param start_id: First ticket id.
    :param end_id: Last ticket id.
    :param source: Passed to :func:`download_one_id`.
    """
    # One directory listing instead of an existence check per id.
    existing = set(os.listdir(data_dir))
    for ticket_id in tqdm.tqdm(range(start_id, end_id + 1)):
        fname = f"{ticket_id}.json"
        if fname in existing:
            continue
        json_data = download_one_id(ticket_id, source=source)
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
    parser.add_argument("--source", type=str, default="auto", choices=["auto", "api", "web"],
                        help="Fetch source: auto (API fallback to web), api, or web.")
    args = parser.parse_args()

    os.makedirs(args.data_dir, exist_ok=True)
    download_range(args.data_dir, args.start_id, args.end_id, args.source)


if __name__ == "__main__":
    main()

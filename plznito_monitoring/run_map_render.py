# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Render Plznito tickets into a self-contained Leaflet map HTML page."""

import argparse
import datetime
import json
import logging
import re
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from plznito_monitoring.common import (
    CATEGORY_IDS,
    CATEGORY_LABELS,
    PLZNITO_TZ,
    STATUS_LABELS,
    atomic_open,
    load_json,
    parse_int,
    status_label,
)

logger = logging.getLogger(__name__)
PLZNITO_DIR = Path(__file__).parent


DATE_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%d.%m.%Y",
)
DEFAULT_CENTER = [49.7443392, 13.3766164]
DEFAULT_ZOOM = 13
DEFAULT_TIME_FILTER = "last_30_days"
# "compact": name/date/status only; "full": also description, solution and photo embedded in the page;
# "api": like compact, details are loaded from the Plznito API when a popup opens.
POPUP_MODES = ("compact", "full", "api")
DETAILS_API_PATH = "/plznito/api/issues/"
MAP_TEMPLATE_PATH = str(PLZNITO_DIR / "map_template.html")
MAP_CONFIG_PLACEHOLDER = "/*__MAP_CONFIG__*/null"
# Scraped records only have the date of the last status change. Ids are assigned in creation
# order and that date is never before creation, so the earliest date among a ticket and the
# next CREATION_WINDOW ids bounds its creation date (validated on 42k API records: median
# error 1 day, p99 4 days; a short window limits the damage of a mistyped date).
CREATION_WINDOW = 50
# One palette for markers, status chips and popup badges.
STATUS_COLOR_MAP = {
    1: "#2563eb",  # Nové: blue
    2: "#d97706",  # V řešení: amber
    3: "#16a34a",  # Vyřešeno: green
    4: "#dc2626",  # Odmítnuto: red
    5: "#6b7280",  # Nepatří městu: grey
    6: "#0891b2",  # Odpovězeno: teal
}

Record = dict[str, Any]


def _parse_date_value(value: Any) -> datetime.datetime | None:
    """
    Parse a ticket date in one of :data:`DATE_FORMATS`.

    :param value: Raw date value.
    :return: Naive datetime (Prague local time), or ``None`` if it does not parse.
    """
    if value is None:
        return None
    text_value = str(value).strip()
    if not text_value:
        return None
    for fmt in DATE_FORMATS:
        try:
            return datetime.datetime.strptime(text_value, fmt)
        except ValueError:
            continue
    return None


def _parse_created(item: Record) -> datetime.datetime | None:
    """
    Real creation time of an API-shape record.

    :param item: Raw record.
    :return: ``created.date``, or ``None`` for scraped records.
    """
    created = item.get("created")
    return _parse_date_value(created.get("date")) if isinstance(created, dict) else None


def _parse_item_datetime(item: Record) -> tuple[datetime.datetime | None, str | None]:
    """
    Best known date of a record: ``created.date`` (API shape), else ``date`` (scraped shape,
    the date of the last status change).

    :param item: Raw record.
    :return: ``(datetime, original text)``, or ``(None, None)``.
    """
    date_candidates = []
    created = item.get("created")
    if isinstance(created, dict):
        date_candidates.append(created.get("date"))
    date_candidates.append(item.get("date"))

    for raw_date in date_candidates:
        parsed = _parse_date_value(raw_date)
        if parsed is not None:
            return parsed, str(raw_date).strip()
    return None, None


def estimate_creation_dates(items: list[Any]) -> dict[int, datetime.datetime]:
    """
    Estimated creation time of every record without a real ``created`` date, see :data:`CREATION_WINDOW`.

    :param items: All raw records (the estimate needs the newer neighbours of each record).
    :return: Estimate by ticket id, only for records without ``created``.
    """
    dated = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = parse_int(item.get("id"))
        date_obj, _ = _parse_item_datetime(item)
        if item_id is not None and date_obj is not None:
            dated.append((item_id, date_obj, _parse_created(item) is None))
    dated.sort(key=lambda entry: entry[0])

    estimates = {}
    for index, (item_id, _, needs_estimate) in enumerate(dated):
        if needs_estimate:
            estimates[item_id] = min(date_obj for _, date_obj, _ in dated[index : index + CREATION_WINDOW + 1])
    return estimates


def _parse_coordinate(value: Any) -> float | None:
    """
    Parse a latitude/longitude value (number or numeric string).

    :param value: Raw value.
    :return: Float, or ``None`` for empty, ``"None"`` and non-numeric values.
    """
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped.lower() == "none":
            return None
        value = stripped
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_optional_label(value: Any) -> str | None:
    """
    Non-empty label from a string or a ``{"name"|"title"|"label": ...}`` dict.

    :param value: Raw value.
    :return: Stripped label, or ``None``.
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
        return None
    if isinstance(value, dict):
        for key in ("name", "title", "label"):
            raw = value.get(key)
            if not isinstance(raw, str):
                continue
            stripped = raw.strip()
            if stripped:
                return stripped
    return None


def _resolve_category(category_id: int | None, category_value: Any) -> tuple[int | None, str, str]:
    """
    Category of a ticket; official names are mapped to their id so both record shapes match.

    :param category_id: Category id (API shape).
    :param category_value: Category name or object (scraped shape).
    :return: ``(id, filter key, label)``, e.g. ``(4, "id:4", "Odpadky, černé skládky, úklidy")``;
        unknown names give ``(None, "name:<name>", name)``.
    """
    category_name = _extract_optional_label(category_value)
    if category_id is None and category_name is not None:
        category_id = CATEGORY_IDS.get(category_name)

    if category_id is not None:
        return category_id, f"id:{category_id}", CATEGORY_LABELS.get(category_id, f"Kategorie {category_id}")
    if category_name is not None:
        return None, f"name:{category_name}", category_name
    return None, "unknown", "Neznámá kategorie"


def _to_unix_timestamp(value: datetime.datetime) -> int:
    """
    Unix timestamp; naive values are taken as Prague local time.

    :param value: Datetime.
    :return: Seconds since the epoch.
    """
    if value.tzinfo is None:
        return int(value.replace(tzinfo=PLZNITO_TZ).timestamp())
    return int(value.astimezone(datetime.UTC).timestamp())


def _sanitize_photo_url(item: Record) -> str | None:
    """
    URL of the first photo: an absolute ``thumb`` URL (API shape) or an S3 path (scraped shape).

    :param item: Raw record.
    :return: Absolute http(s) URL, or ``None``.
    """
    photos = item.get("photos")
    if not isinstance(photos, list) or not photos:
        return None
    photo = photos[0]

    # Handle case where photo is a dictionary with "thumb" key
    if isinstance(photo, dict):
        photo_url = photo.get("thumb")
        if not isinstance(photo_url, str):
            return None
        if not photo_url.startswith(("http://", "https://")):
            return None
        return photo_url

    # Handle case where photo is a string path
    if isinstance(photo, str):
        photo_url = photo.strip()
        if not photo_url:
            return None
        # If it's a relative path, prepend the base URL
        if not photo_url.startswith(("http://", "https://")):
            photo_url = f"https://tf-prod-plznito-web.s3.eu-central-1.amazonaws.com/{photo_url}"
        return photo_url

    return None


def normalize_item(
    item: Any, created_estimates: dict[int, datetime.datetime] | None = None
) -> tuple[Record | None, str | None]:
    """
    Normalise a raw record (API or scraped shape) into the fields used by the map and the API.

    :param item: Raw record.
    :param created_estimates: Output of :func:`estimate_creation_dates`; used for records
        without a real creation date.
    :return: ``(normalized record, None)``, or ``(None, skip reason)`` for records without id,
        status, valid date or coordinates.
    """
    if not isinstance(item, dict):
        return None, "missing_required_fields"

    item_id = item.get("id")
    if item_id is None:
        return None, "missing_required_fields"

    status_id = parse_int(item.get("status_id"))
    if status_id is None:
        return None, "missing_required_fields"

    date_time_obj, date_text = _parse_item_datetime(item)
    if date_time_obj is None:
        return None, "invalid_date"
    item_key = parse_int(item_id)
    estimate = created_estimates.get(item_key) if created_estimates and item_key is not None else None
    created_is_estimate = estimate is not None
    if estimate is not None:
        date_time_obj = estimate
        date_text = "≈ " + estimate.strftime("%d.%m.%Y")

    latitude = _parse_coordinate(item.get("latitude"))
    longitude = _parse_coordinate(item.get("longitude"))
    if latitude is None or longitude is None:
        return None, "invalid_coordinates"

    category_id, category_key, category_label = _resolve_category(
        parse_int(item.get("category_id")), item.get("category")
    )

    normalized = {
        "id": item_id,
        "status_id": status_id,
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "solution": item.get("solution"),
        "date_obj": date_time_obj,
        "date_text": date_text,
        "created_is_estimate": created_is_estimate,
        "latitude": latitude,
        "longitude": longitude,
        "photo_url": _sanitize_photo_url(item),
        "category_id": category_id,
        "category_key": category_key,
        "category_label": category_label,
    }
    return normalized, None


def _item_id_for_skip(item: Any) -> str | None:
    """
    Id of a skipped record for the skip report.

    :param item: Raw record.
    :return: Id as text, or ``None``.
    """
    if not isinstance(item, dict):
        return None
    item_id = item.get("id")
    if item_id is None:
        return None
    return str(item_id)


def _serialize_marker(item: Record, popup_mode: str) -> Record:
    """
    Marker data for the page script.

    :param item: Normalised record.
    :param popup_mode: One of :data:`POPUP_MODES`; ``"full"`` adds description, solution and photo.
    :return: Marker dict.
    """
    ticket_id = str(item["id"])
    marker_data = {
        "id": ticket_id,
        "status_id": item["status_id"],
        "status_label": status_label(item["status_id"]),
        "category_key": item["category_key"],
        "category_label": item["category_label"],
        "name": "" if item["name"] is None else str(item["name"]),
        "date_text": item["date_text"],
        "date_ts": _to_unix_timestamp(item["date_obj"]),
        "year": item["date_obj"].year,
        "lat": item["latitude"],
        "lon": item["longitude"],
        "ticket_url": f"https://www.plznito.cz/map/{quote(ticket_id, safe='')}",
    }

    if popup_mode == "full":
        marker_data["description"] = "" if item["description"] is None else str(item["description"])
        marker_data["solution"] = "" if item["solution"] is None else str(item["solution"])
        marker_data["photo_url"] = item["photo_url"]

    return marker_data


def _status_option_sort_key(option: dict[str, str]) -> int:
    """
    Sort statuses numerically, non-numeric keys last.

    :param option: ``{"key", "label"}`` option.
    :return: Sort key.
    """
    key = option["key"]
    return int(key) if key.isdigit() else 10**9


def _category_option_sort_key(option: dict[str, str]) -> tuple[int, int | str]:
    """
    Sort categories: by id first, then by name, unknown last.

    :param option: ``{"key", "label"}`` option.
    :return: Sort key.
    """
    key = option["key"]
    if key.startswith("id:"):
        category_id = key.split(":", 1)[1]
        if category_id.isdigit():
            return 0, int(category_id)
        return 0, category_id
    if key.startswith("name:"):
        return 1, option["label"].lower()
    return 2, option["label"].lower()


def serialize_map_data(
    data_current: list[Any],
    popup_mode: str = "compact",
    now: datetime.datetime | None = None,
) -> dict[str, Any]:
    """
    Turn raw records into the data embedded in the map page.

    :param data_current: Raw records.
    :param popup_mode: One of :data:`POPUP_MODES`.
    :param now: Reference time for the "recent" layers (naive Prague time); defaults to now.
    :raises ValueError: For an unknown ``popup_mode``.
    :return: Markers, layers, filter options, skip report and stats.
    """
    if popup_mode not in POPUP_MODES:
        raise ValueError(f"popup_mode must be one of {POPUP_MODES}.")

    if now is None:
        # Naive Prague time, the same frame as the ticket dates it is compared with.
        now = datetime.datetime.now(PLZNITO_TZ).replace(tzinfo=None)

    recent_cutoff_7 = now - datetime.timedelta(days=7)
    recent_cutoff_30 = now - datetime.timedelta(days=30)
    stats: Counter[str] = Counter()
    markers = []
    skipped = []
    years = set()
    status_options_map = {}
    category_options_map = {}

    created_estimates = estimate_creation_dates(data_current)
    for item in data_current:
        stats["input_records"] += 1

        normalized_item, error_reason = normalize_item(item, created_estimates)
        if normalized_item is None:
            stats[f"skipped_{error_reason}"] += 1
            skipped.append({"id": _item_id_for_skip(item), "reason": error_reason})
            logger.debug("Skipping record due to %s: %r", error_reason, item)
            continue

        marker_data = _serialize_marker(normalized_item, popup_mode=popup_mode)

        if normalized_item["date_obj"] > recent_cutoff_7:
            stats["added_last_7_days"] += 1

        if normalized_item["date_obj"] > recent_cutoff_30:
            marker_data["layer"] = "recent"
            stats["added_last_30_days"] += 1
        else:
            year = normalized_item["date_obj"].year
            marker_data["layer"] = str(year)
            years.add(year)
            stats["added_year_layer"] += 1

        status_key = str(marker_data["status_id"])
        if status_key not in status_options_map:
            status_options_map[status_key] = {
                "key": status_key,
                "label": marker_data["status_label"],
            }

        category_key = marker_data["category_key"]
        if category_key not in category_options_map:
            category_options_map[category_key] = {
                "key": category_key,
                "label": marker_data["category_label"],
            }

        markers.append(marker_data)

    stats["valid_rendered"] = len(markers)
    stats["skipped_total"] = stats["input_records"] - stats["valid_rendered"]
    status_options = sorted(status_options_map.values(), key=_status_option_sort_key)
    category_options = sorted(category_options_map.values(), key=_category_option_sort_key)

    return {
        "markers": markers,
        "years": sorted(years),
        "status_options": status_options,
        "category_options": category_options,
        "skipped": skipped,
        "stats": stats,
        "popup_mode": popup_mode,
        "generated_ts": _to_unix_timestamp(now),
    }


def _json_for_inline_script(data: Any) -> str:
    """
    JSON safe to embed in a ``<script>`` block.

    :param data: JSON-serialisable value.
    :return: Compact JSON with ``</`` escaped.
    """
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def _minify_html(html_text: str) -> str:
    """
    Drop comments and whitespace between tags; ``script``/``style``/``pre``/``textarea`` stay untouched.

    :param html_text: Page HTML.
    :return: Minified HTML.
    """
    block_pattern = re.compile(r"(?is)<(script|style|pre|textarea)\b.*?</\1>")
    preserved_blocks: list[str] = []

    def _preserve_block(match: re.Match[str]) -> str:
        block_id = len(preserved_blocks)
        preserved_blocks.append(match.group(0))
        return f"__HTML_MINIFY_BLOCK_{block_id}__"

    minified = block_pattern.sub(_preserve_block, html_text)
    minified = re.sub(r"<!--(?!\s*\[if).*?-->", "", minified, flags=re.DOTALL)
    minified = re.sub(r">\s+<", "><", minified)
    minified = minified.strip()

    for block_id, block_text in enumerate(preserved_blocks):
        minified = minified.replace(f"__HTML_MINIFY_BLOCK_{block_id}__", block_text)

    return minified


def _build_map_html(serialized_data: dict[str, Any], cluster: bool = False) -> str:
    """
    Fill :data:`MAP_TEMPLATE_PATH` with the map data.

    :param serialized_data: Output of :func:`serialize_map_data`.
    :param cluster: Cluster markers.
    :raises ValueError: If the template does not contain the config placeholder exactly once.
    :return: Page HTML.
    """
    config = {
        "markers": serialized_data["markers"],
        "years": serialized_data["years"],
        "statusOptions": serialized_data["status_options"],
        "categoryOptions": serialized_data["category_options"],
        "generatedTs": serialized_data["generated_ts"],
        "popupMode": serialized_data["popup_mode"],
        "detailsApiPath": DETAILS_API_PATH,
        "useCluster": cluster,
        "defaultTimeFilter": DEFAULT_TIME_FILTER,
        "center": DEFAULT_CENTER,
        "zoom": DEFAULT_ZOOM,
        "statusColors": {str(key): value for key, value in STATUS_COLOR_MAP.items()},
        "statusLabels": {str(key): value for key, value in STATUS_LABELS.items()},
    }
    with open(MAP_TEMPLATE_PATH, encoding="utf-8") as fr:
        template = fr.read()
    if template.count(MAP_CONFIG_PLACEHOLDER) != 1:
        raise ValueError(f"{MAP_TEMPLATE_PATH} must contain {MAP_CONFIG_PLACEHOLDER} exactly once.")
    return template.replace(MAP_CONFIG_PLACEHOLDER, _json_for_inline_script(config))


def get_map(data_current: list[Any], cluster: bool = False, popup_mode: str = "compact") -> str:
    """
    Build the map page and log a processing summary.

    :param data_current: Raw records.
    :param cluster: Cluster markers.
    :param popup_mode: One of :data:`POPUP_MODES`.
    :return: Page HTML.
    """
    serialized_data = serialize_map_data(data_current, popup_mode=popup_mode)
    stats = serialized_data["stats"]

    logger.info(
        (
            "Map processing summary: input=%d, valid_rendered=%d, added_last_7=%d, added_last_30=%d, "
            "added_year_layer=%d, skipped_total=%d, skipped_missing_required_fields=%d, "
            "skipped_invalid_date=%d, skipped_invalid_coordinates=%d."
        ),
        stats["input_records"],
        stats["valid_rendered"],
        stats["added_last_7_days"],
        stats["added_last_30_days"],
        stats["added_year_layer"],
        stats["skipped_total"],
        stats["skipped_missing_required_fields"],
        stats["skipped_invalid_date"],
        stats["skipped_invalid_coordinates"],
    )

    return _build_map_html(serialized_data, cluster=cluster)


def render_map_to_file(
    file_in: str = str(PLZNITO_DIR / "plznito_cyklo.json"),
    file_out: str = str(PLZNITO_DIR / "templates" / "plznito_map.html"),
    cluster: bool = False,
    popup_mode: str = "compact",
) -> None:
    """
    Render a JSON DB file into a map page, written atomically.

    :param file_in: JSON list of records, or ``{"items": [...]}``.
    :param file_out: Output HTML path.
    :param cluster: Cluster markers.
    :param popup_mode: One of :data:`POPUP_MODES`.
    :raises ValueError: If the input has another shape.
    """
    logger.info("Loading data from %s", file_in)
    data = load_json(file_in)

    if isinstance(data, dict) and isinstance(data.get("items"), list):
        data_records = data["items"]
    elif isinstance(data, list):
        data_records = data
    else:
        raise ValueError("Input JSON must be a list of records or an object with an 'items' list.")

    logger.info("Rendering map from %d records", len(data_records))
    map_html = get_map(data_records, cluster=cluster, popup_mode=popup_mode)
    map_html = _minify_html(map_html)

    with atomic_open(file_out) as fw:
        fw.write(map_html)
    logger.info("Saved map to %s", file_out)


def main() -> None:
    """Command line entry point."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log_level", type=str, default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    )
    parser.add_argument("--file_in", type=str, default=str(PLZNITO_DIR / "plznito_cyklo.json"))
    parser.add_argument("--file_out", type=str, default=str(PLZNITO_DIR / "templates" / "plznito_map.html"))
    parser.add_argument("--cluster_style", action="store_true")
    parser.add_argument("--popup_mode", type=str, default="compact", choices=POPUP_MODES)
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level.upper(), logging.INFO)
    logging.basicConfig(
        filename=str(PLZNITO_DIR / "plznito_monitoring.log"), level=log_level, format='%(asctime)s %(message)s'
    )
    logger.setLevel(log_level)

    render_map_to_file(args.file_in, args.file_out, cluster=args.cluster_style, popup_mode=args.popup_mode)


if __name__ == "__main__":
    main()

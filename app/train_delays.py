# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Scraper of train delays from the Babitron pages (zponline / zponlineos)."""

import logging
import re
import unicodedata
from typing import Any

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

USER_AGENT = "plznito-monitoring/1.0 (+https://github.com/plzennakole/plznito-monitoring)"
TRAIN_ID_RE = re.compile(r"\b([A-Za-z]{1,6})\s*([0-9]{1,6})\b")
TIME_RE = re.compile(r"\b([0-2]?\d:[0-5]\d)\b")


class TrainDelaysScrapeError(Exception):
    """Raised when a Babitron page cannot be downloaded."""


def get_text(html: str) -> str:
    """
    :param html: HTML fragment.
    :return: Its text content, stripped.
    """
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text().strip()


def normalize_text(text: str) -> str:
    """
    :param text: Text.
    :return: Lower-cased text without diacritics.
    """
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(char for char in normalized if not unicodedata.combining(char)).lower()


def extract_inner_html(cell: Tag) -> str:
    """
    :param cell: Table cell.
    :return: Inner HTML of the cell, stripped.
    """
    return "".join(str(child) for child in cell.contents).strip()


def get_delay(text: str) -> int | None:
    """
    Delay in minutes.

    :param text: Delay cell HTML.
    :return: Minutes (0 when on time), or ``None`` when unknown, cancelled, diverted or disrupted.
    """
    if not text:
        return None
    return parse_delay_status_and_minutes(text)[1]


def parse_delay_status_and_minutes(delay_text: str) -> tuple[str, int | None]:
    """
    Classify a delay cell.

    :param delay_text: Delay cell HTML.
    :return: ``(status, minutes)``; status is ``on_time``, ``delayed``, ``canceled``, ``diverted``,
        ``disruption`` or ``unknown``.
    """
    text_norm = normalize_text(get_text(delay_text))
    if "bez zpozdeni" in text_norm or "vcas" in text_norm:
        return "on_time", 0
    if "zrusen" in text_norm:
        return "canceled", None
    if "odklon" in text_norm:
        return "diverted", None
    if "vyluka" in text_norm:
        return "disruption", None

    for token in text_norm.split():
        cleaned = token.strip(".,;")
        match = re.fullmatch(r"\+?(\d{1,3})", cleaned)
        if match:
            return "delayed", int(match.group(1))
    return "unknown", None


def parse_train_identity(train_text: str) -> tuple[str | None, int | None]:
    """
    :param train_text: Train cell text, e.g. ``"Os 7401"``.
    :return: ``(category, number)``, or ``(None, None)``.
    """
    match = TRAIN_ID_RE.search(train_text)
    if not match:
        return None, None
    return match.group(1), int(match.group(2))


def parse_scheduled_actual_times(scheduled_actual_text: str) -> tuple[str | None, str | None]:
    """
    :param scheduled_actual_text: Time cell text, e.g. ``"10:05 (10:12)"``.
    :return: ``(scheduled HH:MM, actual HH:MM)``; missing values are ``None``.
    """
    times = TIME_RE.findall(scheduled_actual_text)
    if not times:
        return None, None
    if len(times) == 1:
        return times[0], None
    return times[0], times[1]


def source_page_from_url(url: str) -> str:
    """
    :param url: Babitron page URL.
    :return: ``"zponlineos"`` or ``"zponline"``.
    """
    lowered = url.lower().rstrip("/")
    if lowered.endswith(".html"):
        lowered = lowered[: -len(".html")]
    if lowered.endswith("zponlineos"):
        return "zponlineos"
    return "zponline"


def scrape_babitron_delays(url: str) -> dict[str, dict[str, Any]]:
    """
    Scrape all trains from a Babitron page.

    :param url: Page URL.
    :raises TrainDelaysScrapeError: If the page does not return HTTP 200.
    :return: Train details keyed by the train cell text.
    """
    results: dict[str, dict[str, Any]] = {}
    source_page = source_page_from_url(url)

    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code != 200:
        raise TrainDelaysScrapeError(f"Chyba při stahování stránky: {response.status_code}")

    # Babitron neposílá charset v hlavičce, stránky jsou ale v UTF-8.
    response.encoding = "utf-8"
    # html5lib korektně uzavírá implicitně neukončené <TD>, html.parser je vnořuje.
    soup = BeautifulSoup(response.text, "html5lib")
    tables = soup.find_all("table", {"align": "CENTER", "bgcolor": "0000ff"})

    if not tables:
        logger.warning("Tabulka se zpožděními nebyla nalezena: %s", url)
        return results

    for table in tables:
        rows = table.find_all("tr")
        for row in rows[1:]:
            cells = row.find_all("td")
            if len(cells) < 6:
                continue

            train_info = get_text(str(cells[0]))
            if not train_info:
                continue
            train_name = extract_inner_html(cells[1])
            route = extract_inner_html(cells[2])
            station = extract_inner_html(cells[3])
            scheduled_actual_time = extract_inner_html(cells[4])
            delay_text = extract_inner_html(cells[5])
            delay_info = get_delay(delay_text)
            status, delay_minutes = parse_delay_status_and_minutes(delay_text)
            train_category, train_number = parse_train_identity(train_info)
            route_text = get_text(route)
            station_text = get_text(station)
            scheduled_text = get_text(scheduled_actual_time)
            scheduled_time_hhmm, actual_time_hhmm = parse_scheduled_actual_times(scheduled_text)

            results[train_info] = {
                "train": train_info,
                "name": train_name,
                "route": route,
                "station": station,
                "scheduled_actual_time": scheduled_actual_time,
                "delay_text": delay_text,
                "delay": delay_info,
                "status": status,
                "delay_minutes": delay_minutes,
                "train_category": train_category,
                "train_number": train_number,
                "route_text": route_text,
                "station_text": station_text,
                "scheduled_time_hhmm": scheduled_time_hhmm,
                "actual_time_hhmm": actual_time_hhmm,
                "source_page": source_page,
            }

    return results


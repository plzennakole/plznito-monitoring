# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""In-memory store of Plznito issues, reloaded whenever the JSON DB file changes."""

import datetime
import json
import logging
import os
import pathlib
import threading
from collections import Counter
from typing import Any

from plznito_monitoring.common import PLZNITO_TZ, parse_int, status_label
from plznito_monitoring.cyklo_filter import is_cyklo_item
from plznito_monitoring.run_map_render import estimate_creation_dates, normalize_item

logger = logging.getLogger(__name__)

_PLZNITO_DIR = pathlib.Path(__file__).parent.parent / 'plznito_monitoring'
DEFAULT_DB_PATH = _PLZNITO_DIR / 'plznito_all.json'


class IssueNotFoundError(Exception):
    """Raised when an issue id is not present in the store."""


def db_path_from_env() -> pathlib.Path:
    """
    Resolve the Plznito JSON DB path from ``PLZNITO_DB_JSON`` or the repo default.

    :return: Path to the JSON DB file.
    """
    value = (os.getenv('PLZNITO_DB_JSON') or '').strip()
    return pathlib.Path(value) if value else DEFAULT_DB_PATH


def to_api_issue(
    item: dict[str, Any], created_estimates: dict[int, datetime.datetime] | None = None
) -> dict[str, Any] | None:
    """
    Convert a raw Plznito record (API or web-scrape shape) into the public API shape.

    :param item: Raw record as stored in the JSON DB.
    :param created_estimates: Creation date estimates for scraped records, see
        :func:`plznito_monitoring.run_map_render.estimate_creation_dates`.
    :return: API issue dict, or ``None`` if the record lacks id, status, date or coordinates.
    """
    normalized, _skip_reason = normalize_item(item, created_estimates)
    if normalized is None:
        return None
    issue_id = parse_int(normalized['id'])
    if issue_id is None:
        return None

    status_id = normalized['status_id']
    address = item.get('address')
    return {
        'id': issue_id,
        'name': normalized['name'] or '',
        'description': normalized['description'] or '',
        'solution': normalized['solution'] or '',
        'status_id': status_id,
        'status_label': status_label(status_id),
        'category_id': normalized['category_id'],
        'category_label': normalized['category_label'],
        'created': normalized['date_obj'].replace(tzinfo=PLZNITO_TZ),
        'created_is_estimate': normalized['created_is_estimate'],
        'latitude': normalized['latitude'],
        'longitude': normalized['longitude'],
        'address': (address.strip() or None) if isinstance(address, str) else None,
        'photo_url': normalized['photo_url'],
        'is_cyklo': is_cyklo_item(item),
        'ticket_url': f"https://www.plznito.cz/map/{issue_id}",
    }


def filter_issues(
    issues: list[dict[str, Any]],
    since: datetime.date | None = None,
    until: datetime.date | None = None,
    status_id: int | None = None,
    category_id: int | None = None,
    cyklo: bool | None = None,
    since_id: int | None = None,
) -> list[dict[str, Any]]:
    """
    Filter API issues; all given conditions must hold. Order is preserved.

    :param issues: API issues as returned by :meth:`IssueStore.get_issues`.
    :param since: Keep issues created on or after this date (local Prague date).
    :param until: Keep issues created on or before this date (local Prague date).
    :param status_id: Keep issues with this status id.
    :param category_id: Keep issues with this category id.
    :param cyklo: Keep only cycling (``True``) or only non-cycling (``False``) issues.
    :param since_id: Keep issues with id strictly greater than this (incremental sync).
    :return: Matching issues.
    """
    return [
        issue
        for issue in issues
        if (since is None or issue['created'].date() >= since)
        and (until is None or issue['created'].date() <= until)
        and (status_id is None or issue['status_id'] == status_id)
        and (category_id is None or issue['category_id'] == category_id)
        and (cyklo is None or issue['is_cyklo'] == cyklo)
        and (since_id is None or issue['id'] > since_id)
    ]


class IssueStore:
    """
    Plznito issues held in memory, reloaded when the JSON DB file's mtime changes.

    The cron job rewrites the file atomically every few hours, so the mtime is a
    reliable cache key and one ``stat`` per request is all the invalidation needed.
    """

    def __init__(self, path: pathlib.Path) -> None:
        """
        :param path: Path to the JSON DB file (list of raw Plznito records).
        """
        self.path = path
        self._mtime: float | None = None
        self._issues: list[dict[str, Any]] = []
        self._by_id: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _reload_if_changed(self) -> float:
        """
        Reload the JSON DB file if its mtime differs from the loaded one.

        :raises FileNotFoundError: If the JSON DB file does not exist.
        :return: mtime of the loaded file.
        """
        mtime = os.stat(self.path).st_mtime
        if mtime == self._mtime:
            return mtime
        with self._lock:
            if mtime == self._mtime:
                return mtime
            with open(self.path, encoding='utf-8') as fr:
                raw_items = json.load(fr)

            issues = []
            skipped: Counter[str] = Counter()
            created_estimates = estimate_creation_dates(raw_items)
            for item in raw_items:
                issue = to_api_issue(item, created_estimates)
                if issue is None:
                    skipped['invalid'] += 1
                    continue
                issues.append(issue)
            issues.sort(key=lambda issue: (issue['created'], issue['id']), reverse=True)

            self._issues = issues
            self._by_id = {issue['id']: issue for issue in issues}
            self._mtime = mtime
            logger.info("Loaded %d Plznito issues from %s (skipped %d)", len(issues), self.path, skipped['invalid'])
        return mtime

    def get_issues(self) -> list[dict[str, Any]]:
        """
        Return all valid issues, newest first.

        :raises FileNotFoundError: If the JSON DB file does not exist.
        :return: API issues.
        """
        self._reload_if_changed()
        return self._issues

    def get_issue(self, issue_id: int) -> dict[str, Any]:
        """
        Return a single issue by id.

        :param issue_id: Plznito ticket id.
        :raises IssueNotFoundError: If there is no such issue.
        :return: API issue.
        """
        self._reload_if_changed()
        issue = self._by_id.get(issue_id)
        if issue is None:
            raise IssueNotFoundError(f"Issue {issue_id} not found")
        return issue

    @property
    def updated_at(self) -> datetime.datetime:
        """
        :return: Last modification time of the loaded JSON DB file (UTC).
        """
        mtime = self._reload_if_changed()
        return datetime.datetime.fromtimestamp(mtime, tz=datetime.UTC)

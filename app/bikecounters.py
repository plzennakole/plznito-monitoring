# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Cycling counters: single-page app and its JSON API backed by the ``cyklo.db`` SQLite database."""

import logging
import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from flask import Blueprint, Response, abort, jsonify, request

from app.cors import add_cors_headers
from app.extensions import cache
from app.settings import env_flag, env_int
from bikecounters_web import config as bw_cfg
from bikecounters_web import yoy as bw_yoy

logger = logging.getLogger(__name__)

_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_MAX_DATE_RANGE_DAYS = env_int('BIKECOUNTERS_MAX_DATE_RANGE_DAYS', 730)
_MAX_RESULT_ROWS = env_int('BIKECOUNTERS_MAX_RESULT_ROWS', 50_000)
# SQL expression truncating ``ts`` to the bucket of each resolution.
_RESOLUTION_TRUNC = {
    'hourly': 'substr(ts, 1, 13)',  # 'YYYY-MM-DD HH'
    'daily': 'substr(ts, 1, 10)',  # 'YYYY-MM-DD'
}

bp = Blueprint('bikecounters', __name__, url_prefix='/bikecounters')
bp.after_request(add_cors_headers)


def query(sql: str, params: list[Any] | tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    """
    Run a read query on the counters database.

    :param sql: SQL with ``?`` placeholders.
    :param params: Placeholder values.
    :return: Rows as dicts.
    """
    with sqlite3.connect(bw_cfg.DB_PATH) as db:
        db.execute('PRAGMA busy_timeout=5000')
        db.row_factory = sqlite3.Row
        rows = db.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _get_location(loc_id: str) -> dict[str, Any]:
    """
    Location config by id, or abort with 404.

    :param loc_id: Location id.
    :return: Location config.
    """
    loc = bw_cfg.LOCATION_BY_ID.get(loc_id)
    if not loc:
        abort(404)
    return loc


@bp.route('')
def page() -> Response:
    """Cycling counters SPA."""
    html_path = bw_cfg.BASE_DIR / 'templates' / 'index.html'
    return Response(html_path.read_text(encoding='utf-8'), mimetype='text/html')


@bp.route('/api/nav')
def api_nav() -> Response:
    """Navigation tree for the sidebar (ECO-counter + cameras)."""
    sections: dict[str, list[dict[str, Any]]] = {}
    for loc in bw_cfg.LOCATIONS:
        sections.setdefault(loc['section'], []).append(loc)

    eco_items = [
        {'id': loc['id'], 'name': loc['name'], 'icon': loc.get('icon', ''), 'color': loc['color']}
        for loc in sections.get('eco', [])
    ]

    # Cameras: ungrouped items and groups (in order of first appearance)
    cam_items: list[dict[str, Any]] = []
    seen_groups = set()
    for loc in sections.get('camera', []):
        gid = loc.get('group')
        if not gid:
            cam_items.append(
                {
                    'type': 'item',
                    'id': loc['id'],
                    'name': loc['name'],
                    'icon': loc.get('icon', ''),
                    'color': loc['color'],
                }
            )
            continue
        if gid in seen_groups:
            continue
        seen_groups.add(gid)
        group_info = bw_cfg.GROUPS.get(gid, {'label': gid})
        cam_items.append(
            {
                'type': 'group',
                'id': gid,
                'name': group_info['label'],
                'icon': group_info.get('icon', ''),
                'children': [
                    {'id': location['id'], 'name': location['name'], 'color': location['color']}
                    for location in bw_cfg.LOCATIONS
                    if location.get('group') == gid
                ],
            }
        )

    return jsonify(
        [
            {'label': "ECO-COUNTER", 'items': eco_items},
            {'label': "KAMERY", 'items': cam_items},
        ]
    )


@bp.route('/api/location/<loc_id>')
def api_location(loc_id: str) -> Response:
    """Location metadata including collectors."""
    loc = _get_location(loc_id)
    return jsonify(
        {
            'id': loc['id'],
            'name': loc['name'],
            'color': loc['color'],
            'type': loc['type'],
            'collectors': loc['collectors'],
        }
    )


def _validate_date_range(from_date: str | None, to_date: str | None) -> None:
    """
    Abort with 400 unless both dates are valid ``YYYY-MM-DD`` (if given) and form a short enough range.

    :param from_date: Range start, inclusive.
    :param to_date: Range end, inclusive.
    """
    for name, value in (('from', from_date), ('to', to_date)):
        if not value:
            continue
        if not _DATE_RE.match(value):
            abort(400, description=f"Invalid '{name}' date. Use YYYY-MM-DD.")
        try:
            date.fromisoformat(value)
        except ValueError:
            abort(400, description=f"Invalid '{name}' date value.")
    if from_date and to_date:
        span = (date.fromisoformat(to_date) - date.fromisoformat(from_date)).days
        if span < 0:
            abort(400, description="'from' must not be after 'to'.")
        if span > _MAX_DATE_RANGE_DAYS:
            abort(400, description=f"Date range exceeds maximum of {_MAX_DATE_RANGE_DAYS} days.")


def _counts_payload(loc_id: str, resolution: str, from_date: str | None, to_date: str | None) -> dict[str, Any]:
    """
    Aggregated counts of a location, combined and per collector.

    :param loc_id: Location id (404 if unknown).
    :param resolution: ``"hourly"``; anything else means ``"daily"``.
    :param from_date: Optional range start ``YYYY-MM-DD``, inclusive.
    :param to_date: Optional range end ``YYYY-MM-DD``, inclusive.
    :return: ``{resolution, combined, collectors, truncated}``; ``truncated`` is true when the
        row limit cut the result.
    """
    collectors = _get_location(loc_id)['collectors']
    if resolution != 'hourly':
        resolution = 'daily'
    if not collectors:
        return {'resolution': resolution, 'combined': [], 'collectors': [], 'truncated': False}

    _validate_date_range(from_date, to_date)
    trunc = _RESOLUTION_TRUNC[resolution]

    source_ids = [c['source_id'] for c in collectors]
    placeholders = ','.join('?' * len(source_ids))

    date_clause = ''
    date_params = []
    if from_date:
        date_clause += ' AND ts >= ?'
        date_params.append(from_date)
    if to_date:
        # Include the full to_date day
        date_clause += " AND ts < date(?, '+1 day')"
        date_params.append(to_date)

    combined_rows = query(
        f"""
        SELECT {trunc}       AS period,
               SUM(bikes)    AS bikes,
               SUM(scooters) AS scooters
        FROM counts
        WHERE source_id IN ({placeholders}) {date_clause}
        GROUP BY period
        ORDER BY period
        LIMIT {_MAX_RESULT_ROWS}
        """,
        source_ids + date_params,
    )
    per_col_rows = query(
        f"""
        SELECT source_id,
               {trunc}       AS period,
               SUM(bikes)    AS bikes,
               SUM(scooters) AS scooters
        FROM counts
        WHERE source_id IN ({placeholders}) {date_clause}
        GROUP BY source_id, period
        ORDER BY source_id, period
        LIMIT {_MAX_RESULT_ROWS}
        """,
        source_ids + date_params,
    )

    truncated = len(combined_rows) >= _MAX_RESULT_ROWS or len(per_col_rows) >= _MAX_RESULT_ROWS
    if truncated:
        logger.warning(
            "api_counts %s/%s hit the %d row limit; result is truncated.", loc_id, resolution, _MAX_RESULT_ROWS
        )

    per_col_grouped: dict[str, list[dict[str, Any]]] = {}
    for row in per_col_rows:
        per_col_grouped.setdefault(row['source_id'], []).append(
            {'ts': row['period'], 'bikes': row['bikes'], 'scooters': row['scooters']}
        )

    return {
        'resolution': resolution,
        # 'period' is called 'ts' in the frontend
        'combined': [{'ts': r['period'], 'bikes': r['bikes'], 'scooters': r['scooters']} for r in combined_rows],
        'collectors': [
            {
                'source_id': col['source_id'],
                'label': col['label'],
                'color': col['color'],
                'data': per_col_grouped.get(col['source_id'], []),
            }
            for col in collectors
        ],
        'truncated': truncated,
    }


@bp.route('/api/counts/<loc_id>')
def api_counts(loc_id: str) -> Response:
    """
    Counts for a location; query ``resolution=hourly|daily``, optional ``from``/``to`` (YYYY-MM-DD).

    The ``ts`` field is ``YYYY-MM-DD HH`` (hourly) or ``YYYY-MM-DD`` (daily).
    """
    return jsonify(
        _counts_payload(
            loc_id,
            request.args.get('resolution', 'daily'),
            request.args.get('from'),
            request.args.get('to'),
        )
    )


@bp.route('/api/daily/<loc_id>')
def api_daily(loc_id: str) -> Response:
    """Daily totals (combined + per collector); alias used by the frontend for the initial load."""
    return jsonify(_counts_payload(loc_id, 'daily', request.args.get('from'), request.args.get('to')))


@bp.route('/api/weather')
def api_weather() -> Response:
    """Weather data as {date: {t, p}}."""
    rows = query('SELECT date, t, p FROM weather ORDER BY date')
    return jsonify({r['date']: {'t': r['t'], 'p': r['p']} for r in rows})


def _daily_combined(source_ids: list[str]) -> list[dict[str, Any]]:
    """
    Combined daily totals of the given sources.

    :param source_ids: Collector source ids.
    :return: Rows ``{ts, bikes, scooters}`` ordered by day.
    """
    placeholders = ','.join('?' * len(source_ids))
    return query(
        f"""
        SELECT substr(ts, 1, 10) AS ts,
               SUM(bikes)        AS bikes,
               SUM(scooters)     AS scooters
        FROM counts
        WHERE source_id IN ({placeholders})
        GROUP BY substr(ts, 1, 10)
        ORDER BY ts
        """,
        source_ids,
    )


def _location_yoy(loc: dict[str, Any], ref_date: date) -> dict[str, Any]:
    """
    Year-over-year stats of a location up to and including ``ref_date``.

    :param loc: Location config.
    :param ref_date: Last day to include.
    :return: ``{monthly, mtd, ytd}``.
    """
    source_ids = [c['source_id'] for c in loc['collectors']]
    daily = bw_yoy.daily_totals(_daily_combined(source_ids)) if source_ids else {}
    return {
        'monthly': bw_yoy.add_monthly_yoy(bw_yoy.monthly_stats(daily)),
        'mtd': bw_yoy.period_yoy(daily, ref_date.replace(day=1), ref_date),
        'ytd': bw_yoy.period_yoy(daily, ref_date.replace(month=1, day=1), ref_date),
    }


def _yoy_ref_date() -> date:
    """
    :return: Last complete day (yesterday).
    """
    return date.today() - timedelta(days=1)


@bp.route('/api/yoy/<loc_id>')
def api_yoy_location(loc_id: str) -> Response:
    """Year-over-year monthly avg/day + YTD, MTD for a location."""
    loc = _get_location(loc_id)
    ref_date = _yoy_ref_date()
    return jsonify({'ref_date': ref_date.isoformat(), **_location_yoy(loc, ref_date)})


@bp.route('/api/yoy')
@cache.cached(timeout=3600)
def api_yoy_all() -> Response:
    """Year-over-year summary (YTD, MTD) for all locations."""
    ref_date = _yoy_ref_date()
    result = []
    for loc in bw_cfg.LOCATIONS:
        if not loc['collectors']:
            continue
        stats = _location_yoy(loc, ref_date)
        result.append(
            {
                'id': loc['id'],
                'name': loc['name'],
                'type': loc['type'],
                'color': loc['color'],
                'ytd': stats['ytd'],
                'mtd': stats['mtd'],
            }
        )
    return jsonify({'ref_date': ref_date.isoformat(), 'locations': result})


@bp.route('/api/debug/<loc_id>')
def api_debug(loc_id: str) -> Response:
    """Raw DB stats for a location (only with BIKECOUNTERS_ENABLE_DEBUG_API)."""
    if not env_flag('BIKECOUNTERS_ENABLE_DEBUG_API'):
        abort(404)

    source_ids = [c['source_id'] for c in _get_location(loc_id)['collectors']]
    if not source_ids:
        return jsonify({'location': loc_id, 'sources': [], 'latest_rows': []})
    placeholders = ','.join('?' * len(source_ids))

    stats = query(
        f"""
        SELECT source_id,
               COUNT(*)        AS intervals,
               MIN(ts)         AS first_ts,
               MAX(ts)         AS last_ts,
               SUM(bikes)      AS total_bikes,
               SUM(scooters)   AS total_scooters,
               MAX(bikes)      AS max_interval_bikes
        FROM counts
        WHERE source_id IN ({placeholders})
        GROUP BY source_id
    """,
        source_ids,
    )

    sample = query(
        f"""
        SELECT source_id, ts, bikes, scooters
        FROM counts
        WHERE source_id IN ({placeholders})
        ORDER BY ts DESC LIMIT 10
    """,
        source_ids,
    )

    return jsonify({'location': loc_id, 'sources': stats, 'latest_rows': sample})

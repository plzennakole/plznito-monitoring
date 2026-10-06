"""
yoy.py — Year-over-year comparison of daily counter totals.

Days with a zero total are treated as missing (camera/counter outage), so
comparisons are made on average per day (monthly) or on matched days only
(periods), never on raw sums over uneven coverage.
"""
from datetime import date, timedelta

MIN_MONTH_DAYS = 15


def daily_totals(rows: list[dict]) -> dict[date, int]:
    """
    Convert daily API rows into a ``{date: bikes + scooters}`` mapping.

    :param rows: Rows with ``'ts'`` (``YYYY-MM-DD...``), ``'bikes'`` and ``'scooters'``.
    :return: Totals per day; days with a zero total are dropped.
    """
    result = {}
    for row in rows:
        total = row['bikes'] + row['scooters']
        if total > 0:
            result[date.fromisoformat(row['ts'][:10])] = total
    return result


def monthly_stats(daily: dict[date, int]) -> list[dict]:
    """
    Aggregate daily totals into per-month statistics.

    :param daily: Totals per day, as returned by :func:`daily_totals`.
    :return: Sorted list of ``{'year', 'month', 'days', 'total', 'avg'}``.
    """
    months = {}
    for day, total in daily.items():
        entry = months.setdefault((day.year, day.month), {'days': 0, 'total': 0})
        entry['days'] += 1
        entry['total'] += total
    return [
        {
            'year': year,
            'month': month,
            'days': entry['days'],
            'total': entry['total'],
            'avg': round(entry['total'] / entry['days'], 1),
        }
        for (year, month), entry in sorted(months.items())
    ]


def _pct_change(current: float, previous: float) -> "float | None":
    """
    Compute a percentage change rounded to one decimal.

    :param current: Current value.
    :param previous: Previous value.
    :return: Change in percent, or ``None`` if ``previous`` is zero.
    """
    if previous == 0:
        return None
    return round((current / previous - 1) * 100, 1)


def add_monthly_yoy(stats: list[dict], min_days: int = MIN_MONTH_DAYS) -> list[dict]:
    """
    Add ``'pct'`` (change of avg/day vs. the same month a year earlier) to each entry.

    :param stats: Monthly statistics, as returned by :func:`monthly_stats`.
    :param min_days: Minimum days with data required in both months.
    :return: New list of entries with ``'pct'`` set, or ``None`` if not comparable.
    """
    by_month = {(s['year'], s['month']): s for s in stats}
    result = []
    for s in stats:
        prev = by_month.get((s['year'] - 1, s['month']))
        pct = None
        if prev and s['days'] >= min_days and prev['days'] >= min_days:
            pct = _pct_change(s['avg'], prev['avg'])
        result.append({**s, 'pct': pct})
    return result


def period_yoy(daily: dict[date, int], start: date, end: date) -> dict:
    """
    Compare a period with the same period a year earlier, on matched days only.

    A day counts only if it has data in both years; 29 February is skipped.

    :param daily: Totals per day, as returned by :func:`daily_totals`.
    :param start: First day of the period (inclusive).
    :param end: Last day of the period (inclusive).
    :return: ``{'current', 'previous', 'days', 'pct'}``.
    """
    current = previous = days = 0
    day = start
    while day <= end:
        if day in daily and not (day.month == 2 and day.day == 29):
            prev_day = day.replace(year=day.year - 1)
            if prev_day in daily:
                current += daily[day]
                previous += daily[prev_day]
                days += 1
        day += timedelta(days=1)
    return {
        'current': current,
        'previous': previous,
        'days': days,
        'pct': _pct_change(current, previous),
    }

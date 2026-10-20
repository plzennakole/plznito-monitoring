# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Shared filtering helpers for cycling-related ticket records."""

from typing import Any


def to_lower_text(value: Any) -> str:
    """
    Lower-case text of a record field.

    :param value: Raw field value.
    :return: Lower-cased ``str(value)``, or ``""`` for ``None``.
    """
    if value is None:
        return ""
    return str(value).lower()


def is_cyklo_record(report_text: str, name_text: str) -> bool:
    """
    Keyword check on already lower-cased texts.

    :param report_text: Lower-cased ticket report.
    :param name_text: Lower-cased ticket name.
    :return: Whether the texts mention cycling or scooters.
    """
    return (
        "cykl" in report_text
        or "kolob" in report_text
        or "cikli" in report_text
        or "cyklo" in name_text
        or "kolob" in name_text
    )


def is_cyklo_item(item: Any) -> bool:
    """
    Whether a raw record is cycling-related ("recykl..." in the report does not count).

    :param item: Raw record.
    :return: ``True`` for cycling-related dict records.
    """
    if not isinstance(item, dict):
        return False

    report_text = to_lower_text(item.get("report"))
    name_text = to_lower_text(item.get("name"))
    if not report_text and not name_text:
        return False

    return is_cyklo_record(report_text, name_text) and "recykl" not in report_text


def filter_cyklo_items(items: list[Any]) -> list[dict[str, Any]]:
    """
    Keep only cycling-related records.

    :param items: Raw records.
    :return: Records for which :func:`is_cyklo_item` holds.
    """
    return [item for item in items if is_cyklo_item(item)]

"""Shared filtering helpers for cycling-related ticket records."""


def to_lower_text(value):
    if value is None:
        return ""
    return str(value).lower()


def is_cyklo_record(report_text, name_text):
    return (
        "cykl" in report_text
        or "kolob" in report_text
        or "cikli" in report_text
        or "cyklo" in name_text
        or "kolob" in name_text
    )


def is_cyklo_item(item):
    if not isinstance(item, dict):
        return False

    report_text = to_lower_text(item.get("report"))
    name_text = to_lower_text(item.get("name"))
    if not report_text and not name_text:
        return False

    return is_cyklo_record(report_text, name_text) and "recykl" not in report_text


def filter_cyklo_items(items):
    return [item for item in items if is_cyklo_item(item)]

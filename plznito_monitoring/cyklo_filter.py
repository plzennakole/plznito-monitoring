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


def filter_cyklo_items(items):
    data_cyklo = []
    for item in items:
        if not isinstance(item, dict):
            continue

        report_text = to_lower_text(item.get("report"))
        name_text = to_lower_text(item.get("name"))
        if not report_text and not name_text:
            continue

        if is_cyklo_record(report_text, name_text) and "recykl" not in report_text:
            data_cyklo.append(item)

    return data_cyklo

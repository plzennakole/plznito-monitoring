# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Helpers shared by the Plznito scripts and the API: parsing, labels and JSON file I/O."""

import contextlib
import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterator
from typing import Any, TextIO
from zoneinfo import ZoneInfo

# Ticket dates are naive Prague local times.
PLZNITO_TZ = ZoneInfo('Europe/Prague')

# One label per status id; raw records mix API ids with scraped texts ("Zodpovězené", ...).
STATUS_LABELS = {
    1: "Nové",
    2: "V řešení",
    3: "Vyřešeno",
    4: "Odmítnuto",
    5: "Nepatří městu",
    6: "Odpovězeno",
}


# Official categories, from the category filter on plznito.cz. API records carry the id,
# scraped records the name; both are mapped to the same category.
CATEGORY_LABELS = {
    1: "Dětská hřiště",
    2: "Ostatní",
    3: "Silnice, cyklostezky",
    4: "Odpadky, černé skládky, úklidy",
    5: "Chodník",
    6: "Kanalizace",
    7: "Dopravní značení",
    8: "Lavičky",
    9: "Veřejné osvětlení",
    10: "Veřejná zeleň",
    11: "Autovrak",
    12: "Zastávky MHD",
}
CATEGORY_IDS = {label: category_id for category_id, label in CATEGORY_LABELS.items()}


def status_label(status_id: int) -> str:
    """
    Human-readable label of a Plznito status.

    :param status_id: Plznito status id.
    :return: Known label, or ``"Stav <id>"`` for unknown ids.
    """
    return STATUS_LABELS.get(status_id, f"Stav {status_id}")


def setup_logging(log_file: str, level: int = logging.INFO) -> None:
    """
    Log to ``log_file`` at ``level``, and warnings/errors also to stderr.

    Cron mails and deploy logs then show only problems, the file has the full story.

    :param log_file: Log file path.
    :param level: Level for the log file.
    """
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(level)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    logging.basicConfig(
        level=level,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=[file_handler, stderr_handler],
    )


def parse_int(value: Any) -> int | None:
    """
    Parse an id-like value (int or digit string) into an int.

    :param value: Raw value from a record.
    :return: The integer, or ``None`` for booleans, non-digit strings and other types.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit():
            return int(stripped)
    return None


def load_json(path: str) -> Any:
    """
    Load a UTF-8 JSON file.

    :param path: File path.
    :return: Parsed JSON value.
    """
    with open(path, encoding='utf-8') as fr:
        return json.load(fr)


@contextlib.contextmanager
def atomic_open(path: str) -> Iterator[TextIO]:
    """
    Open a text file for writing atomically: readers see either the old or the new content,
    never a partial file. The parent directory is created if missing.

    :param path: Target file path.
    :return: Context manager yielding the file handle; the file is replaced on successful exit.
    """
    target_dir = os.path.dirname(os.path.abspath(path))
    os.makedirs(target_dir, exist_ok=True)

    fd, temp_path = tempfile.mkstemp(prefix='.tmp_', dir=target_dir)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fw:
            yield fw
        os.replace(temp_path, path)
    except BaseException:
        os.remove(temp_path)
        raise


def atomic_write_json(path: str, data: Any, indent: int | None = None) -> None:
    """
    Write UTF-8 JSON atomically, see :func:`atomic_open`.

    :param path: Target file path.
    :param data: JSON-serialisable value.
    :param indent: Indentation for human-readable output; ``None`` (default) writes compact JSON.
    """
    separators = None if indent is not None else (',', ':')
    with atomic_open(path) as fw:
        json.dump(data, fw, indent=indent, separators=separators, ensure_ascii=False)

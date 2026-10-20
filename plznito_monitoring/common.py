# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Helpers shared by the Plznito scripts and the API: parsing, labels and JSON file I/O."""

import contextlib
import json
import os
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


def status_label(status_id: int) -> str:
    """
    Human-readable label of a Plznito status.

    :param status_id: Plznito status id.
    :return: Known label, or ``"Stav <id>"`` for unknown ids.
    """
    return STATUS_LABELS.get(status_id, f"Stav {status_id}")


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


def atomic_write_json(path: str, data: Any, indent: int | None = 4) -> None:
    """
    Write JSON atomically, see :func:`atomic_open`.

    :param path: Target file path.
    :param data: JSON-serialisable value.
    :param indent: Indentation passed to :func:`json.dump`; ``None`` for compact output.
    """
    with atomic_open(path) as fw:
        json.dump(data, fw, indent=indent)

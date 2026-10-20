# Copyright 2026 Plzeň na kole Inc. All rights reserved.
"""Environment-based settings; ``.env`` is loaded once, before any module reads the environment."""

import os

from dotenv import load_dotenv

load_dotenv()


def env_int(name: str, default: int) -> int:
    """
    Integer environment variable.

    :param name: Variable name.
    :param default: Value used when the variable is unset, empty or not an integer.
    :return: The integer value.
    """
    value = (os.getenv(name) or '').strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_flag(name: str) -> bool:
    """
    Boolean environment variable (``1``, ``true``, ``yes``, ``on``; case-insensitive).

    :param name: Variable name.
    :return: Whether the flag is set.
    """
    return os.getenv(name, '').lower() in ('1', 'true', 'yes', 'on')

"""
Persistence configuration from environment variables.

- MORALSTACK_DB_PATH: path to SQLite database
- MORALSTACK_PERSIST_MODE: db_only | dual | file_only
- MORALSTACK_UI_USERNAME, MORALSTACK_UI_PASSWORD: for UI Basic Auth
"""

from __future__ import annotations

import os
from typing import Literal

PersistMode = Literal["db_only", "dual", "file_only"]

_DB_PATH_ENV = "MORALSTACK_DB_PATH"
_PERSIST_MODE_ENV = "MORALSTACK_PERSIST_MODE"
_UI_USERNAME_ENV = "MORALSTACK_UI_USERNAME"
_UI_PASSWORD_ENV = "MORALSTACK_UI_PASSWORD"


def get_db_path() -> str | None:
    """Returns the database path from env, or None if not set."""
    path = os.getenv(_DB_PATH_ENV)
    return path.strip() if path else None


def get_persist_mode() -> PersistMode:
    """
    Returns the persistence mode.

    Default: if MORALSTACK_DB_PATH is set -> db_only, else file_only.
    Override with MORALSTACK_PERSIST_MODE.
    """
    raw = os.getenv(_PERSIST_MODE_ENV, "").strip().lower()
    if raw in ("db_only", "dual", "file_only"):
        return raw  # type: ignore[return-value]
    db_path = get_db_path()
    return "db_only" if db_path else "file_only"


def get_ui_credentials() -> tuple[str, str]:
    """Returns (username, password) for UI Basic Auth from env."""
    username = os.getenv(_UI_USERNAME_ENV, "").strip()
    password = os.getenv(_UI_PASSWORD_ENV, "").strip()
    return username, password

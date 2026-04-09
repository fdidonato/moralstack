"""
Observability configuration from environment variables.

New vars:
  MORALSTACK_OBSERVABILITY_MODE    — db_only | dual | file_only
  MORALSTACK_OBSERVABILITY_DB_PATH — path to SQLite database
  MORALSTACK_OBSERVABILITY_JSONL_DIR — directory for JSONL output (default: logs/observability)

Backwards-compat aliases (trigger DeprecationWarning on use):
  MORALSTACK_PERSIST_MODE  -> MORALSTACK_OBSERVABILITY_MODE
  MORALSTACK_DB_PATH       -> MORALSTACK_OBSERVABILITY_DB_PATH

UI auth (unchanged):
  MORALSTACK_UI_USERNAME, MORALSTACK_UI_PASSWORD
"""

from __future__ import annotations

import logging
import os
from typing import Literal

ObservabilityMode = Literal["db_only", "dual", "file_only"]

_OBS_MODE_ENV = "MORALSTACK_OBSERVABILITY_MODE"
_OBS_DB_PATH_ENV = "MORALSTACK_OBSERVABILITY_DB_PATH"
_OBS_JSONL_DIR_ENV = "MORALSTACK_OBSERVABILITY_JSONL_DIR"

# Legacy aliases
_LEGACY_MODE_ENV = "MORALSTACK_PERSIST_MODE"
_LEGACY_DB_PATH_ENV = "MORALSTACK_DB_PATH"

_UI_USERNAME_ENV = "MORALSTACK_UI_USERNAME"
_UI_PASSWORD_ENV = "MORALSTACK_UI_PASSWORD"

_DEFAULT_JSONL_DIR = "logs/observability"

_logger = logging.getLogger(__name__)
_legacy_warned: set[str] = set()


def _warn_legacy(old: str, new: str) -> None:
    if old not in _legacy_warned:
        _legacy_warned.add(old)
        _logger.warning(
            "moralstack: env var %s is deprecated; use %s instead.",
            old,
            new,
        )


def get_db_path() -> str | None:
    """Returns the database path. Prefers MORALSTACK_OBSERVABILITY_DB_PATH."""
    path = os.getenv(_OBS_DB_PATH_ENV, "").strip()
    if path:
        return path
    legacy = os.getenv(_LEGACY_DB_PATH_ENV, "").strip()
    if legacy:
        _warn_legacy(_LEGACY_DB_PATH_ENV, _OBS_DB_PATH_ENV)
        return legacy
    return None


def get_observability_mode() -> ObservabilityMode:
    """
    Returns the observability mode. Prefers MORALSTACK_OBSERVABILITY_MODE.

    Default: db_only if DB_PATH is set, else file_only.
    """
    raw = os.getenv(_OBS_MODE_ENV, "").strip().lower()
    if raw in ("db_only", "dual", "file_only"):
        return raw  # type: ignore[return-value]
    legacy = os.getenv(_LEGACY_MODE_ENV, "").strip().lower()
    if legacy in ("db_only", "dual", "file_only"):
        _warn_legacy(_LEGACY_MODE_ENV, _OBS_MODE_ENV)
        return legacy  # type: ignore[return-value]
    return "db_only" if get_db_path() else "file_only"


# Keep old name as alias used by persistence/config.py wrapper
get_persist_mode = get_observability_mode


def get_jsonl_dir() -> str:
    """Returns the JSONL output directory."""
    return os.getenv(_OBS_JSONL_DIR_ENV, _DEFAULT_JSONL_DIR).strip() or _DEFAULT_JSONL_DIR


def get_ui_credentials() -> tuple[str, str]:
    """Returns (username, password) for UI Basic Auth from env."""
    username = os.getenv(_UI_USERNAME_ENV, "").strip()
    password = os.getenv(_UI_PASSWORD_ENV, "").strip()
    return username, password

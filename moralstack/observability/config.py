"""
Observability configuration: explicit ``GovernanceConfig`` values, then environment variables.

Precedence for the getters below: explicit value applied via ``apply_explicit_config`` > env var >
default. Explicit values are process-wide and may only *establish* the observability target (the
first ``apply_explicit_config`` call in the process, before the write-queue target is bound); a later
different value is refused with a WARNING and never redirects a live target (the write-queue worker
keeps one SQLite connection for its lifetime, so a redirect would split parent and child rows across
databases). ``"off"``/blank/``None`` mean "not set here": inherit env/defaults (they do not disable
writing). Env vars are still read live on every getter call.

Env vars:
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

import contextlib
import logging
import os
import sys
import threading
from typing import Any, Literal

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

_VALID_MODES = ("db_only", "dual", "file_only")

# Explicit values (keys: observability_mode, db_path, jsonl_dir). Published by rebinding a new dict in one
# assignment so lock-free readers (the getters) never see a half-populated state.
_explicit: dict[str, str] = {}
_applied = False
_explicit_lock = threading.Lock()
_explicit_warned: set[tuple[str, str]] = set()


def _warn_legacy(old: str, new: str) -> None:
    if old not in _legacy_warned:
        _legacy_warned.add(old)
        _logger.warning(
            "moralstack: env var %s is deprecated; use %s instead.",
            old,
            new,
        )


def _explicit_value(key: str) -> str | None:
    """Lock-free lookup of an explicit value; never raises, ignores anything that is not a non-blank str."""
    try:
        value = _explicit.get(key)
    except Exception:
        return None
    return value if isinstance(value, str) and value.strip() else None


def get_db_path() -> str | None:
    """Returns the database path: explicit > MORALSTACK_OBSERVABILITY_DB_PATH > legacy alias."""
    explicit = _explicit_value("db_path")
    if explicit:
        return explicit
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
    Returns the observability mode: explicit > MORALSTACK_OBSERVABILITY_MODE > legacy alias.

    Default: db_only if DB_PATH (explicit or env) is set, else file_only.
    """
    explicit = _explicit_value("observability_mode")
    if explicit in _VALID_MODES:
        return explicit  # type: ignore[return-value]
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
    """Returns the JSONL output directory: explicit > MORALSTACK_OBSERVABILITY_JSONL_DIR > default."""
    explicit = _explicit_value("jsonl_dir")
    if explicit:
        return explicit
    return os.getenv(_OBS_JSONL_DIR_ENV, _DEFAULT_JSONL_DIR).strip() or _DEFAULT_JSONL_DIR


def _warn_explicit_once(key: str, value: str, message: str, *args: Any) -> None:
    marker = (key, value)
    if marker in _explicit_warned:
        return
    _explicit_warned.add(marker)
    _logger.warning(message, *args)


def _normalize_explicit(key: str, value: Any, source: str) -> str | None:
    """Normalize one explicit value; ``None`` means "not set here" (inherit env/defaults)."""
    if value is None:
        return None
    try:
        text = os.fspath(value)
    except TypeError:
        text = None
    if not isinstance(text, str):
        _warn_explicit_once(
            key,
            repr(value),
            "moralstack: GovernanceConfig.%s=%r (source=%s) is not a str or path; ignored.",
            key,
            value,
            source,
        )
        return None
    text = text.strip()
    if key == "observability_mode":
        text = text.lower()
        if text == "off":
            return None
        if text and text not in _VALID_MODES:
            _warn_explicit_once(
                key,
                text,
                "moralstack: GovernanceConfig.observability_mode=%r (source=%s) is not one of %s; ignored.",
                text,
                source,
                list(_VALID_MODES),
            )
            return None
    return text or None


def _try_establish(values: dict[str, str]) -> bool:
    """Establish ``values`` unless the write-queue target is bound; True when established.

    Check and publication run while holding ``service._obs_lock`` (when the service module is loaded) so
    the singleton cannot be created in between. The service module is looked up lazily to avoid a circular
    import and to avoid importing it just to check.

    Residual race: ``_enqueue`` does not take ``_obs_lock``, so a non-SDK emitter can enqueue on an already
    existing singleton between the bound check and the publish; no SDK/proxy client exists yet while
    ``_applied`` is false, so the window is accepted.
    """
    global _explicit
    service = sys.modules.get("moralstack.observability.service")
    lock = getattr(service, "_obs_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        instance = getattr(service, "_obs_instance", None)
        if instance is not None and instance.target_bound:
            return False
        _explicit = dict(values)
        return True


def apply_explicit_config(
    *,
    mode: Any,
    db_path: Any,
    jsonl_dir: Any,
    source: str,
) -> None:
    """Apply explicit ``GovernanceConfig`` observability values process-wide. Never raises.

    ``None``/blank/``"off"`` mean "not set here" (inherit env/defaults). Values are established only by
    the first call in the process and only while the write-queue target is not bound; afterwards a
    different value is ignored with a WARNING (once per key and value) and an equal one is a no-op.
    """
    global _applied
    try:
        with _explicit_lock:
            raw = {"observability_mode": mode, "db_path": db_path, "jsonl_dir": jsonl_dir}
            normalized = {key: _normalize_explicit(key, value, source) for key, value in raw.items()}
            values = {key: value for key, value in normalized.items() if value}
            first = not _applied
            _applied = True
            if first and _try_establish(values):
                return
            cause = (
                "observability target already bound" if first else "target already fixed by an earlier govern()/create_app"
            )
            effective = {
                "observability_mode": get_observability_mode,
                "db_path": get_db_path,
                "jsonl_dir": get_jsonl_dir,
            }
            for key, value in values.items():
                current = effective[key]()
                if value == current:
                    continue
                _warn_explicit_once(
                    key,
                    value,
                    "moralstack: GovernanceConfig.%s=%r (source=%s) ignored: %s; effective value stays %r.",
                    key,
                    value,
                    source,
                    cause,
                    current,
                )
    except Exception:
        _logger.warning("moralstack: apply_explicit_config failed; env/defaults stay in effect.", exc_info=True)


def _reset_explicit_config() -> None:
    """Clear the explicit state (tests only)."""
    global _explicit, _applied
    with _explicit_lock:
        _explicit = {}
        _applied = False
        _explicit_warned.clear()


def get_ui_credentials() -> tuple[str, str]:
    """Returns (username, password) for UI Basic Auth from env."""
    username = os.getenv(_UI_USERNAME_ENV, "").strip()
    password = os.getenv(_UI_PASSWORD_ENV, "").strip()
    return username, password

"""
Generic environment-variable helpers for MoralStack configuration.

Provides type-safe readers for float, int, str, and bool values from
os.environ.  Missing or empty values fall back to the caller-supplied
default.  Optional min/max clamping is available for numeric types.

All per-module config loaders (risk, critic, simulator, hindsight,
perspective, orchestrator) delegate to these functions.
"""

from __future__ import annotations

import os


def get_env_float(
    key: str,
    default: float,
    min_val: float | None = None,
    max_val: float | None = None,
) -> float:
    """Read a float from env; if missing or empty, return *default*.

    Optionally clamp the parsed value to [min_val, max_val].
    """
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        val = float(raw)
    except ValueError:
        return default
    if min_val is not None and val < min_val:
        return min_val
    if max_val is not None and val > max_val:
        return max_val
    return val


def get_env_int(
    key: str,
    default: int,
    min_val: int | None = None,
    max_val: int | None = None,
) -> int:
    """Read an int from env; if missing or empty, return *default*.

    Optionally enforce min_val / max_val bounds.
    """
    raw = (os.environ.get(key) or "").strip()
    if not raw:
        return default
    try:
        val = int(raw)
    except ValueError:
        return default
    if min_val is not None and val < min_val:
        return min_val
    if max_val is not None and val > max_val:
        return max_val
    return val


def get_env_str(key: str, default: str) -> str:
    """Read a string from env; if missing or empty, return *default*."""
    raw = (os.environ.get(key) or "").strip()
    return raw if raw else default


def get_env_bool(key: str, default: bool) -> bool:
    """Read a bool from env.

    Truthy values: ``1``, ``true``, ``yes`` (case-insensitive).
    Falsy  values: ``0``, ``false``, ``no``.
    If missing, empty, or unrecognised, return *default*.
    """
    raw = (os.environ.get(key) or "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes"):
        return True
    if raw in ("0", "false", "no"):
        return False
    return default

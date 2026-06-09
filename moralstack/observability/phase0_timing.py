"""Temporary Phase 0 timing hooks for token/latency measurement.

Disabled by default. Enable with ``MORALSTACK_PHASE0_TIMING=1`` and optionally
write JSONL records with ``MORALSTACK_PHASE0_TIMING_JSONL``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Mapping

_ENV_ENABLED = "MORALSTACK_PHASE0_TIMING"
_ENV_JSONL = "MORALSTACK_PHASE0_TIMING_JSONL"
_TRUE_VALUES = {"1", "true", "yes", "on"}

logger = logging.getLogger(__name__)


def phase0_timing_enabled() -> bool:
    """Return True when temporary Phase 0 timers are explicitly enabled."""
    return os.getenv(_ENV_ENABLED, "").strip().lower() in _TRUE_VALUES


def emit_phase0_timing(event: str, duration_ms: float, **fields: Any) -> None:
    """Emit one best-effort Phase 0 timing record when the feature flag is on."""
    if not phase0_timing_enabled():
        return

    record: dict[str, Any] = {
        "event": event,
        "duration_ms": round(float(duration_ms), 3),
        "timestamp_ms": int(time.time() * 1000),
    }
    record.update(_json_safe_fields(fields))

    try:
        payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        logger.info("phase0_timing %s", payload)
        _append_jsonl_if_configured(payload)
    except Exception as exc:  # noqa: BLE001
        logger.debug("phase0 timing emit skipped: %s", exc)


def _json_safe_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if value is None or isinstance(value, (str, int, float, bool)):
            safe[key] = value
        elif isinstance(value, (list, tuple)):
            safe[key] = [item if isinstance(item, (str, int, float, bool)) or item is None else str(item) for item in value]
        elif isinstance(value, dict):
            safe[key] = {
                str(k): v if isinstance(v, (str, int, float, bool)) or v is None else str(v) for k, v in value.items()
            }
        else:
            safe[key] = str(value)
    return safe


def _append_jsonl_if_configured(payload: str) -> None:
    path_raw = os.getenv(_ENV_JSONL, "").strip()
    if not path_raw:
        return
    path = Path(path_raw)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(payload)
        handle.write("\n")

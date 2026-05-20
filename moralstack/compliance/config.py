"""
Configuration for the Developer Contract Compliance Layer.

Reads MORALSTACK_DCCL_* environment variables.
Reference: dccl_specification_v0.3.md section 10.

All getters fall back to documented defaults on invalid values, logging a warning.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

_LOG = logging.getLogger(__name__)

# Environment variable names
_ENV_ENABLED = "MORALSTACK_DCCL_ENABLED"
_ENV_EVALUATION_PATH = "MORALSTACK_DCCL_EVALUATION_PATH"
_ENV_LLM_MODEL = "MORALSTACK_DCCL_LLM_MODEL"
_ENV_LLM_TIMEOUT_MS = "MORALSTACK_DCCL_LLM_TIMEOUT_MS"
_ENV_LLM_MAX_TOKENS = "MORALSTACK_DCCL_LLM_MAX_TOKENS"
_ENV_CONFIDENCE_THRESHOLD = "MORALSTACK_DCCL_CONFIDENCE_THRESHOLD"
_ENV_MAX_RULES = "MORALSTACK_DCCL_MAX_RULES_PER_CONTRACT"
_ENV_SAFETY_OVERRIDE_STRICT = "MORALSTACK_DCCL_SAFETY_OVERRIDE_STRICT"

# Defaults
_DEFAULT_ENABLED = True
_DEFAULT_EVALUATION_PATH = "hybrid"
_DEFAULT_LLM_MODEL = "gpt-4o"
_DEFAULT_LLM_TIMEOUT_MS = 5000
_DEFAULT_LLM_MAX_TOKENS = 512
_DEFAULT_CONFIDENCE_THRESHOLD = 0.85
_DEFAULT_MAX_RULES = 100
_DEFAULT_SAFETY_OVERRIDE_STRICT = True

EvaluationPathLiteral = Literal["structured", "llm", "hybrid"]


def _parse_bool(raw: str, default: bool, var_name: str) -> bool:
    """Parse a boolean env var, falling back to default on invalid input."""
    s = (raw or "").strip().lower()
    if not s:
        return default
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    _LOG.warning("invalid bool value for %s: %r, using default %s", var_name, raw, default)
    return default


def _parse_int(raw: str, default: int, var_name: str, min_value: int | None = None) -> int:
    """Parse an integer env var, falling back to default on invalid input."""
    s = (raw or "").strip()
    if not s:
        return default
    try:
        value = int(s)
    except ValueError:
        _LOG.warning("invalid int value for %s: %r, using default %d", var_name, raw, default)
        return default
    if min_value is not None and value < min_value:
        _LOG.warning(
            "value for %s (%d) below minimum %d, using default %d",
            var_name,
            value,
            min_value,
            default,
        )
        return default
    return value


def _parse_float(raw: str, default: float, var_name: str, min_value: float, max_value: float) -> float:
    """Parse a float env var, falling back to default on invalid or out-of-range input."""
    s = (raw or "").strip()
    if not s:
        return default
    try:
        value = float(s)
    except ValueError:
        _LOG.warning("invalid float value for %s: %r, using default %f", var_name, raw, default)
        return default
    if not (min_value <= value <= max_value):
        _LOG.warning(
            "value for %s (%f) out of range [%f, %f], using default %f",
            var_name,
            value,
            min_value,
            max_value,
            default,
        )
        return default
    return value


def get_dccl_enabled() -> bool:
    """Whether the DCCL is active. Default True."""
    return _parse_bool(os.getenv(_ENV_ENABLED, ""), _DEFAULT_ENABLED, _ENV_ENABLED)


def get_dccl_evaluation_path() -> EvaluationPathLiteral:
    """Evaluation path preference: 'structured' | 'llm' | 'hybrid'. Default 'hybrid'."""
    raw = (os.getenv(_ENV_EVALUATION_PATH, "") or "").strip().lower()
    if not raw:
        return _DEFAULT_EVALUATION_PATH  # type: ignore[return-value]
    if raw in ("structured", "llm", "hybrid"):
        return raw  # type: ignore[return-value]
    _LOG.warning(
        "invalid value for %s: %r, using default %s",
        _ENV_EVALUATION_PATH,
        raw,
        _DEFAULT_EVALUATION_PATH,
    )
    return _DEFAULT_EVALUATION_PATH  # type: ignore[return-value]


def get_dccl_llm_model() -> str:
    """Model used by the LLM path. Default 'gpt-4o'."""
    raw = (os.getenv(_ENV_LLM_MODEL, "") or "").strip()
    return raw if raw else _DEFAULT_LLM_MODEL


def get_dccl_llm_timeout_ms() -> int:
    """Timeout in milliseconds for the LLM call. Default 5000."""
    return _parse_int(os.getenv(_ENV_LLM_TIMEOUT_MS, ""), _DEFAULT_LLM_TIMEOUT_MS, _ENV_LLM_TIMEOUT_MS, min_value=100)


def get_dccl_llm_max_tokens() -> int:
    """Max tokens for the LLM response. Default 512."""
    return _parse_int(os.getenv(_ENV_LLM_MAX_TOKENS, ""), _DEFAULT_LLM_MAX_TOKENS, _ENV_LLM_MAX_TOKENS, min_value=64)


def get_dccl_confidence_threshold() -> float:
    """Minimum confidence to accept a MATCH verdict. Default 0.85."""
    return _parse_float(
        os.getenv(_ENV_CONFIDENCE_THRESHOLD, ""),
        _DEFAULT_CONFIDENCE_THRESHOLD,
        _ENV_CONFIDENCE_THRESHOLD,
        min_value=0.0,
        max_value=1.0,
    )


def get_dccl_max_rules_per_contract() -> int:
    """Maximum number of structured rules per contract. Default 100."""
    return _parse_int(os.getenv(_ENV_MAX_RULES, ""), _DEFAULT_MAX_RULES, _ENV_MAX_RULES, min_value=1)


def get_dccl_safety_override_strict() -> bool:
    """Whether safety override blocks at contract loading time. Default True."""
    return _parse_bool(
        os.getenv(_ENV_SAFETY_OVERRIDE_STRICT, ""),
        _DEFAULT_SAFETY_OVERRIDE_STRICT,
        _ENV_SAFETY_OVERRIDE_STRICT,
    )

"""
Provider error classification for LLM API calls (OpenAI and similar).

Classifies exceptions as transient (retry useful) or fatal (do not retry).
Used by policy layer and benchmark for retry/backoff decisions.
Also provides backoff delay with jitter for retry loops.
"""

from __future__ import annotations

import random
import time
from typing import Literal

# Transient: retry with backoff. Fatal: fail immediately.
ProviderErrorKind = Literal["transient", "fatal"]

# Known transient HTTP status codes.
_TRANSIENT_STATUS_CODES = {429, 502, 503, 504}
# Known fatal client-error codes (4xx except 429).
_FATAL_STATUS_CODES_4XX = {400, 401, 403, 404, 422}

# Substrings in error message that indicate transient conditions (fallback when no status_code).
_TRANSIENT_MESSAGE_SUBSTRINGS = (
    "429",
    "502",
    "503",
    "504",
    "rate",
    "quota",
    "overloaded",
    "capacity",
    "timeout",
    "timed out",
    "connection",
    "try again",
)


def classify_provider_error(exc: BaseException) -> ProviderErrorKind:
    """
    Classify a provider/API exception as transient (retry) or fatal (no retry).

    Prefer exception type and status_code when available; use message substring
    matching only as fallback. Policy: transient = 429, 502, 503, 504, timeout,
    rate/quota/overloaded/capacity; fatal = 4xx except 429 (auth, bad request, etc.).

    Returns:
        "transient" if retry with backoff is appropriate, "fatal" otherwise.
    """
    # 1. status_code on exception (e.g. openai.APIStatusError)
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        try:
            code = int(status_code)
        except (TypeError, ValueError):
            pass
        else:
            if code in _TRANSIENT_STATUS_CODES:
                return "transient"
            if 400 <= code < 500 or code >= 500:
                return "fatal"

    # 2. Exception type name (OpenAI client: RateLimitError, Timeout, APIConnectionError, etc.)
    exc_type_name = type(exc).__name__
    if exc_type_name in (
        "RateLimitError",
        "APITimeoutError",
        "TimeoutError",
        "APIConnectionError",
        "ConnectionError",
    ):
        return "transient"
    if exc_type_name in (
        "AuthenticationError",
        "PermissionError",
        "InvalidRequestError",
        "BadRequestError",
    ):
        return "fatal"

    # 3. Message fallback
    msg = (str(exc) or "").lower()
    for sub in _TRANSIENT_MESSAGE_SUBSTRINGS:
        if sub in msg:
            return "transient"
    # 4xx in message often indicates client error
    if "401" in msg or "403" in msg or "400" in msg or "404" in msg:
        return "fatal"

    # Unknown: treat as fatal to avoid unbounded retries.
    return "fatal"


def compute_backoff_delay_sec(
    attempt: int,
    base_delay_sec: float = 2.0,
    max_delay_sec: float = 60.0,
    jitter_max_sec: float = 2.0,
) -> float:
    """
    Compute delay for exponential backoff with jitter (stdlib only).

    delay = min(base * 2^attempt + uniform(0, jitter_max), max_delay)
    """
    delay = base_delay_sec * (2.0**attempt) + random.uniform(0, jitter_max_sec)
    return min(max(delay, 0), max_delay_sec)


def sleep_with_backoff(
    attempt: int,
    base_delay_sec: float = 2.0,
    max_delay_sec: float = 60.0,
    jitter_max_sec: float = 2.0,
) -> None:
    """Sleep for the given attempt using exponential backoff with jitter."""
    time.sleep(
        compute_backoff_delay_sec(
            attempt,
            base_delay_sec=base_delay_sec,
            max_delay_sec=max_delay_sec,
            jitter_max_sec=jitter_max_sec,
        )
    )

"""
Tier-1 detection of enumerated single-token output contracts.

Purpose
-------
Some requests declare that the answer MUST be exactly one token from a small
fixed option set (e.g. boolq-style ``answer exactly 'TRUE' or 'FALSE'``,
multiple-choice ``reply with a single letter``). For such outputs the only way
a "soft" revision (balance/caveat/disclaimer feedback) can change the visible
text is to *flip the selected option*, which corrupts the factual answer.

This module derives a deterministic, LLM-free signal — ``is_enumerated`` plus
the detected ``options`` — from the declared output constraint (developer
contract / system prompt + user turn) cross-checked against the produced draft.
The signal is consumed by the critic gate to suppress SOFT-only revisions on
enumerated answers (HARD violations are never suppressed).

Design constraints:
    * Pure function, no side effects, never raises (best-effort detection).
    * Conservative: fires ONLY when an enumeration is explicitly declared AND
      the draft is a single short token that is a member of that enumeration.
      Either condition alone is insufficient, to avoid false positives on
      free-form answers that merely contain a word like "true".
"""

from __future__ import annotations

import re

# Answer is considered a candidate single-token output only when this short.
_MAX_DRAFT_CHARS = 16

# Instruction keywords that must be present for quoted tokens to count as an
# enumerated answer set (avoids treating arbitrary quotes as options).
_INSTRUCTION_KEYWORDS = (
    "answer",
    "option",
    "select",
    "respond",
    "reply",
    "choose",
    "exactly",
    "one of",
)

# Quoted short tokens, e.g. 'TRUE' / "False" / 'A'.
_QUOTED_RE = re.compile(r"""['"`]([A-Za-z0-9_+-]{1,20})['"`]""")

# Well-known binary sets recognised even when declared without quotes.
_KNOWN_BINARY_SETS: tuple[frozenset[str], ...] = (
    frozenset({"TRUE", "FALSE"}),
    frozenset({"YES", "NO"}),
)


def _normalize(token: str) -> str:
    """Uppercased, stripped of surrounding whitespace/quotes/punctuation."""
    return token.strip().strip("'\"`").strip(".,;:!?)( ").upper()


def _draft_token(draft_text: str) -> str:
    """Return the normalized single-token form of ``draft_text``, or "" if the
    draft is not a single short token (and therefore not an enumerated answer)."""
    stripped = (draft_text or "").strip()
    if not stripped or len(stripped) > _MAX_DRAFT_CHARS:
        return ""
    token = _normalize(stripped)
    # Single token only: no internal whitespace.
    if not token or any(ch.isspace() for ch in token):
        return ""
    return token


def detect_enumerated_output(declared_text: str, draft_text: str) -> tuple[bool, tuple[str, ...]]:
    """
    Detect whether the output contract is a single enumerated token answer.

    Args:
        declared_text: Concatenated declared constraints — typically the
            developer contract / system prompt plus the user turn.
        draft_text: The produced draft answer to cross-check.

    Returns:
        ``(is_enumerated, options)``. ``is_enumerated`` is True only when an
        enumeration is explicitly declared AND the draft is a single short
        token that belongs to it. ``options`` is the detected uppercased set
        (possibly empty when False).
    """
    try:
        token = _draft_token(draft_text)
        if not token:
            return (False, ())

        text = (declared_text or "").lower()
        if not text:
            return (False, ())

        has_instruction = any(kw in text for kw in _INSTRUCTION_KEYWORDS)

        # 1) Explicit quoted option set near an instruction keyword.
        if has_instruction:
            quoted = {
                _normalize(m)
                for m in _QUOTED_RE.findall(declared_text or "")
                if _normalize(m)
            }
            # Single-word, short options only.
            quoted = {q for q in quoted if " " not in q and len(q) <= 20}
            if len(quoted) >= 2 and token in quoted:
                return (True, tuple(sorted(quoted)))

        # 2) Well-known binary sets declared in prose (quotes optional), still
        #    requiring an instruction keyword to be present.
        if has_instruction:
            words = set(re.findall(r"[a-z]+", text))
            for known in _KNOWN_BINARY_SETS:
                if token in known and {m.lower() for m in known}.issubset(words):
                    return (True, tuple(sorted(known)))

        return (False, ())
    except Exception:
        # Best-effort: detection must never break the governance path.
        return (False, ())


__all__ = ["detect_enumerated_output"]

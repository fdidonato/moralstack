"""
Shared helpers for constitution layer (conflict resolution, tokenization).

Used by both store and retriever to avoid circular imports.
"""

from __future__ import annotations

import re
import unicodedata

from moralstack.constitution.schema import Principle

# Language-agnostic stopwords (English-only for compliance with Language-Agnostic invariant).
# Single source of truth for both tokenize() and _extract_keywords_from_description().
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "being",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "may",
        "might",
        "must",
        "of",
        "on",
        "or",
        "should",
        "that",
        "the",
        "them",
        "these",
        "they",
        "this",
        "those",
        "to",
        "was",
        "were",
        "will",
        "with",
        "would",
    }
)


def _specificity_score(principle: Principle) -> float:
    """
    Compute specificity score for a principle.

    Higher = more specific.
    Based on: domain presence, example count, rule length.
    """
    score = 0.0

    if principle.domain is not None:
        score += 10.0

    score += len(principle.examples_deny) * 0.5
    score += len(principle.examples_allow) * 0.3
    score += len(principle.keywords) * 0.2
    score += min(len(principle.rule) / 100, 2.0)

    return score


def resolve_conflict(principles: list[Principle]) -> list[Principle]:
    """
    Order principles for conflict resolution.

    Order: 1) Hard > Soft, 2) higher priority wins, 3) more specific wins,
    4) alphabetical by ID (determinism).
    """
    return sorted(
        principles,
        key=lambda p: (0 if p.level == "hard" else 1, -p.priority, -_specificity_score(p), p.id),
    )


def tokenize(text: str) -> set[str]:
    """
    Tokenize text into normalized word set.

    Language-agnostic: Unicode NFD normalization for accents, English-only stopwords,
    basic plural handling (s-stripping). No hardcoded language-specific content.
    """
    # Unicode NFD: decompose accents into base + combining marks, then strip combining marks
    nfd = unicodedata.normalize("NFD", text)
    text = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    text = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = text.split()

    filtered = [t for t in tokens if t and t not in _STOPWORDS and len(t) > 1]
    result = set(filtered)
    for token in filtered:
        if len(token) > 2 and token.endswith("s"):
            singular = token[:-1]
            if len(singular) > 1:
                result.add(singular)

    return result

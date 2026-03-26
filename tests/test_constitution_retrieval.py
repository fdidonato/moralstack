"""
Behavioral regression tests for constitution retrieval and tokenization.

Covers: tokenize(), _extract_keywords_from_description(), _compute_relevance.
Ensures tokenization is stable and language-agnostic (no Italian stopwords).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from moralstack.constitution.helpers import tokenize
from moralstack.constitution.schema import Principle
from moralstack.constitution.store import (
    ConstitutionStore,
    _compute_relevance,
    _extract_keywords_from_description,
)

# -----------------------------------------------------------------------------
# tokenize() tests
# -----------------------------------------------------------------------------


def test_tokenize_english_stable():
    """Same English input produces same output; no Italian stopwords in stoplist."""
    result = tokenize("manipulation and deception in communication")
    assert "manipulation" in result
    assert "deception" in result
    assert "communication" in result
    assert "and" not in result
    assert "in" not in result
    assert result == tokenize("manipulation and deception in communication")


def test_tokenize_accent_normalization():
    """Unicode NFD normalization: accented chars produce base form."""
    result = tokenize("café naïve")
    assert "cafe" in result
    assert "naive" in result


def test_tokenize_no_italian_stopwords():
    """Italian words no longer in stoplist; they pass through as content."""
    assert "il" in tokenize("il")  # no longer filtered as Italian stopword
    assert "lo" in tokenize("lo")  # no longer filtered as Italian stopword
    assert "manipolazione" in tokenize("manipolazione deception")  # content word kept


def test_tokenize_english_stopwords_filtered():
    """English stopwords are filtered."""
    result = tokenize("the and a are is for")
    assert len(result) == 0


# -----------------------------------------------------------------------------
# _extract_keywords_from_description() tests
# -----------------------------------------------------------------------------


def test_extract_keywords_from_description_deterministic():
    """Fixed English description produces deterministic output using shared stopwords."""
    desc = "Principles for ethical communication and manipulation prevention."
    result = _extract_keywords_from_description(desc)
    assert "principles" in result
    assert "ethical" in result
    assert "communication" in result
    assert "manipulation" in result
    assert "prevention" in result
    assert "and" not in result
    assert "for" not in result
    assert result == _extract_keywords_from_description(desc)


def test_extract_keywords_from_description_empty():
    """Empty or whitespace-only description returns empty list."""
    assert _extract_keywords_from_description("") == []
    assert _extract_keywords_from_description("   ") == []


# -----------------------------------------------------------------------------
# _compute_relevance() tests (behavioral retrieval scoring)
# -----------------------------------------------------------------------------


def test_compute_relevance_keyword_match():
    """Principle with matching keyword gets positive score."""
    principle = Principle(
        id="TEST.1",
        level="hard",
        priority=90,
        title="Test Manipulation",
        rule="Do not manipulate users.",
        keywords=["manipulation", "deception"],
    )
    query_tokens = tokenize("manipulation and covert influence")
    score = _compute_relevance(principle, query_tokens, query_tokens, None)
    assert score > 0


def test_compute_relevance_title_match():
    """Principle with matching title gets positive score."""
    principle = Principle(
        id="TEST.2",
        level="soft",
        priority=80,
        title="Transparency and Disclosure",
        rule="Disclose sources clearly.",
        keywords=[],
    )
    query_tokens = tokenize("transparency disclosure sources")
    score = _compute_relevance(principle, query_tokens, query_tokens, None)
    assert score > 0


def test_compute_relevance_no_match():
    """Principle with no overlap gets zero score."""
    principle = Principle(
        id="TEST.3",
        level="soft",
        priority=70,
        title="Unrelated Topic",
        rule="Something about weather.",
        keywords=["weather", "climate"],
    )
    query_tokens = tokenize("manipulation deception harm")
    score = _compute_relevance(principle, query_tokens, query_tokens, None)
    assert score == 0


# -----------------------------------------------------------------------------
# Retrieval behavioral (requires config)
# -----------------------------------------------------------------------------


def test_retrieval_returns_non_empty_when_config_exists():
    """With real config, get_relevant_principles returns non-empty for relevant query."""
    config_dir = Path(__file__).parent.parent / "config" / "constitution"
    if not (config_dir / "core.yaml").exists():
        pytest.skip("config/constitution/core.yaml not present")

    # Mock DomainAgent._call_openai to avoid real OpenAI calls; return core principle IDs
    def _mock_call_openai(prompt: str) -> list[str]:
        return ["CORE.MANIPULATION.1", "CORE.AUTONOMY.1"]

    store = ConstitutionStore(
        config_dir=config_dir,
        use_enhanced_retrieval=False,  # legacy path
    )
    with patch("moralstack.constitution.retriever.DomainAgent._call_openai", side_effect=_mock_call_openai):
        principles = store.get_relevant_principles(
            "How to manipulate someone without them knowing",
            top_k=5,
        )
    assert len(principles) > 0
    ids = [p.id for p in principles]
    assert all("." in pid for pid in ids), "principle ids should be in DOMAIN.NAME form"

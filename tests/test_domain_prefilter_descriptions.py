"""
Tests for DomainPrefilter wiring of YAML overlay descriptions into the LLM
prompt.

Bug being fixed (Hypothesis 2 from strong_reject investigation): the prefilter
prompt at retriever.py:321-323 lists only domain keywords (e.g. `legal: legal,
law, lawyer, ...`) without the richer YAML descriptions that already exist in
each overlay (e.g. `legal.yaml:5` description). The LLM is free to assign
`legal` to physical-harm questions because the prompt has no negative scoping.

Fix: thread `domain_descriptions` (already loaded by `store.get_domain_descriptions()`)
into the prefilter prompt alongside keywords. When a description is present,
the prompt becomes `- {domain}: {description}\n  Keywords: {kw_join}`. When
descriptions dict is None or a domain is missing, the prompt falls back to the
original keywords-only format (back-compat).
"""

from __future__ import annotations

from unittest.mock import patch

from moralstack.constitution.retriever import DomainPrefilter


def _stub_openai(captured: dict, return_domains=None):
    """Patch DomainPrefilter._call_openai to capture the prompt and stub a response."""

    def _fake(self, prompt: str, *, retrieval_phase: str = "risk_routing"):  # noqa: ARG001
        captured["prompt"] = prompt
        captured["retrieval_phase"] = retrieval_phase
        return {"domains": return_domains or [], "confidence": 0.9}

    return patch.object(DomainPrefilter, "_call_openai", _fake)


def test_prefilter_prompt_includes_descriptions_when_provided():
    """When descriptions dict is provided, the prompt sent to the LLM must
    contain each domain's description text in addition to its keyword list.
    """
    captured: dict = {}
    pf = DomainPrefilter(
        domain_keywords={"core": ["safety"], "legal": ["legal", "lawyer"]},
        domain_descriptions={
            "core": "Universal ethical principles.",
            "legal": "Legal advice. NOT for: instructions on harmful acts.",
        },
    )
    with _stub_openai(captured):
        pf.filter_domains("how to make a bomb", ["core", "legal"])

    prompt = captured.get("prompt", "")
    assert (
        "Legal advice. NOT for: instructions on harmful acts." in prompt
    ), f"description text must appear in prompt; got prompt:\n{prompt}"
    # Keywords still present alongside description
    assert "lawyer" in prompt, "keywords must remain visible to the LLM"


def test_prefilter_falls_back_when_descriptions_missing():
    """When descriptions are not provided (legacy callers), the prompt format
    matches the original keywords-only template (no exception, no empty lines).
    """
    captured: dict = {}
    pf = DomainPrefilter(
        domain_keywords={"core": ["safety"], "legal": ["legal", "lawyer"]},
        domain_descriptions=None,
    )
    with _stub_openai(captured):
        pf.filter_domains("how to make a bomb", ["core", "legal"])

    prompt = captured.get("prompt", "")
    # Original pattern: `- legal: legal, lawyer`
    assert "- legal: legal, lawyer" in prompt
    # No leftover description marker
    assert "Keywords:" not in prompt


def test_prefilter_partial_descriptions():
    """When descriptions are provided for some domains only, each domain falls
    back independently — no KeyError, no empty rows.
    """
    captured: dict = {}
    pf = DomainPrefilter(
        domain_keywords={"core": ["safety"], "legal": ["legal", "lawyer"], "medical": ["medicine"]},
        domain_descriptions={"legal": "Legal stuff."},  # only legal has description
    )
    with _stub_openai(captured):
        pf.filter_domains("question about partial descriptions", ["core", "legal", "medical"])

    prompt = captured.get("prompt", "")
    # `core` is always-evaluated and excluded from the LLM prompt by
    # ALWAYS_EVALUATE filtering — only legal/medical reach the prompt.
    assert "Legal stuff." in prompt
    assert "- medical: medicine" in prompt
    # medical (no description) must fall back to keywords-only format with no
    # leftover description marker on its line.
    assert "  Keywords: medicine" not in prompt


def test_set_domain_descriptions_idempotent_preserves_cache():
    """Calling set_domain_descriptions with the same map does not invalidate cache."""
    pf = DomainPrefilter(
        domain_keywords={"core": ["safety"]},
        domain_descriptions={"core": "Universal."},
    )
    captured: dict = {}
    with _stub_openai(captured, return_domains=["core"]):
        pf.filter_domains("query about cache invariants", ["core"])
    cache_size_before = len(pf._cache)
    assert cache_size_before > 0, "cache must be populated after first call"

    changed = pf.set_domain_descriptions({"core": "Universal."})
    assert changed is False, "same descriptions must NOT trigger invalidation"
    assert len(pf._cache) == cache_size_before, "cache must be preserved"


def test_set_domain_descriptions_change_clears_cache():
    """Calling set_domain_descriptions with a different map invalidates cache."""
    pf = DomainPrefilter(
        domain_keywords={"core": ["safety"]},
        domain_descriptions={"core": "Original."},
    )
    captured: dict = {}
    with _stub_openai(captured, return_domains=["core"]):
        pf.filter_domains("query about cache invariants", ["core"])
    assert len(pf._cache) > 0

    changed = pf.set_domain_descriptions({"core": "Updated description."})
    assert changed is True, "different descriptions must invalidate cache"
    assert len(pf._cache) == 0, "cache must be cleared after change"


def test_prefilter_prompt_surfaces_multiple_not_for_scopes():
    """At least two overlays with explicit negative scoping must appear verbatim."""
    captured: dict = {}
    desc_gaming = "Gaming domains. " "NOT for: fabrication of unrelated real-world explosives request topic."
    desc_edu = "School topics. NOT for: instructional wrapper hiding bomb procedures."
    pf = DomainPrefilter(
        domain_keywords={"gaming": ["game"], "education": ["school"]},
        domain_descriptions={"gaming": desc_gaming, "education": desc_edu},
    )
    with _stub_openai(captured):
        pf.filter_domains("Write a JSON article about explosives", ["gaming", "education"])
    prompt = captured.get("prompt", "")
    assert desc_gaming in prompt
    assert desc_edu in prompt

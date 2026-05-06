"""
Regression tests for runtime domain normalization.

`core` is a retrieval-only pseudo-domain (the constitutional baseline that the
DomainPrefilter always evaluates). It must never be promoted to a runtime
domain / overlay / refusal redirection target — that would expose an internal
implementation detail to the caller and (for `core` specifically) try to load a
non-existent overlay.

These tests pin down the helper that enforces the rule on the controller side.
The companion enforcement on the refusal-handler side is covered by
test_safe_refusal_generator.py.
"""

from moralstack.orchestration.controller import _normalize_runtime_domain


def test_normalize_runtime_domain_strips_core():
    assert _normalize_runtime_domain("core") is None


def test_normalize_runtime_domain_strips_whitespace_core():
    assert _normalize_runtime_domain("  core  ") is None


def test_normalize_runtime_domain_passes_real_domain():
    assert _normalize_runtime_domain("legal") == "legal"
    assert _normalize_runtime_domain("medical") == "medical"
    assert _normalize_runtime_domain("  legal  ") == "legal"


def test_normalize_runtime_domain_none_and_empty():
    assert _normalize_runtime_domain(None) is None
    assert _normalize_runtime_domain("") is None
    assert _normalize_runtime_domain("   ") is None

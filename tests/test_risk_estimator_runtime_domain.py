"""
Regression tests for RiskEstimator._get_principles_context.

Pins down the bug fix where the risk estimator used to make a SECOND, separate
LLM domain detection (`constitution.store.detect_domain`) on top of the
DomainPrefilter. That second detection used a different, more permissive prompt
without the "NOT for harmful/illegal/violent acts" exclusion contract on
`legal`, so for a weapon-construction prompt the prefilter would correctly
return `[]` while the second detector returned `legal`. The `legal` value then
propagated as `RiskEstimation.detected_domain` → `request.user_context.domain_overlay`
→ `overlay_applied` → refusal redirection (the user got "consult an attorney"
text on a weapon-construction refusal).

After the fix:
  - `_get_principles_context` calls `get_relevant_principles(domain=None)` only
  - it derives `runtime_domain` from `constitution_store.get_debug_info()["prefiltered_domains"]`
  - `core` is excluded from the candidate set (retrieval-only pseudo-domain)
  - it never imports or calls `detect_domain`

Unify-constitution-retrieval-single-pass (this change): `_get_principles_context` now
returns a `_PrinciplesContextResult` (attribute access: `.formatted_context`,
`.runtime_domain`, plus the full retrieved `.principles` and retrieval-status flags)
instead of a plain 2-tuple, so it can carry the single upstream retrieval for reuse
by deliberation/critic/fast-path. Tests below were re-anchored to attribute access;
assertions are unchanged.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from moralstack.models.risk.estimator import LLMBasedRiskEstimator


class _FakeConstitutionStore:
    """Minimal fake: returns canned principles + canned debug_info."""

    def __init__(self, prefiltered_domains: list[str]) -> None:
        self._prefiltered_domains = prefiltered_domains
        self.get_relevant_principles_calls: list[dict[str, Any]] = []

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[Any]:
        self.get_relevant_principles_calls.append({"query": query, "top_k": top_k, "domain": domain})
        return []  # empty principles is fine — _get_principles_context still returns runtime_domain

    def get_debug_info(self) -> dict[str, Any]:
        return {"prefiltered_domains": list(self._prefiltered_domains)}


def _make_estimator(prefiltered_domains: list[str]) -> tuple[LLMBasedRiskEstimator, _FakeConstitutionStore]:
    store = _FakeConstitutionStore(prefiltered_domains)
    est = LLMBasedRiskEstimator(policy=None, constitution_store=store)
    return est, store


def test_get_principles_context_returns_none_when_only_core_in_prefiltered():
    """`core` alone is the always-on baseline: runtime_domain must be None."""
    est, _ = _make_estimator(["core"])
    result = est._get_principles_context("any prompt")
    assert result.runtime_domain is None


def test_get_principles_context_returns_none_when_prefiltered_is_empty():
    """No prefiltered domains at all: runtime_domain must be None."""
    est, _ = _make_estimator([])
    result = est._get_principles_context("any prompt")
    assert result.runtime_domain is None


def test_get_principles_context_returns_first_specific_domain():
    """First non-core domain in prefiltered_domains wins."""
    est, _ = _make_estimator(["core", "legal", "medical"])
    result = est._get_principles_context("a real legal question")
    assert result.runtime_domain == "legal"


def test_get_principles_context_handles_specific_only_no_core():
    """Defensive: if core is missing for any reason, still pick the first specific domain."""
    est, _ = _make_estimator(["medical"])
    result = est._get_principles_context("a medical question")
    assert result.runtime_domain == "medical"


def test_get_principles_context_calls_get_relevant_principles_with_domain_none():
    """Single source of truth: estimator must NOT pass a pre-detected domain into retrieval."""
    est, store = _make_estimator(["core"])
    est._get_principles_context("any prompt")
    assert len(store.get_relevant_principles_calls) == 1
    assert store.get_relevant_principles_calls[0]["domain"] is None


def test_get_principles_context_does_not_invoke_legacy_detect_domain(monkeypatch):
    """Hard guard: the legacy `detect_domain` LLM classifier must NOT be called from
    the runtime risk-estimator path. Patch it with a sentinel that raises if invoked.
    """
    from moralstack.constitution import store as store_module

    def _boom(*_a, **_k):
        raise AssertionError("detect_domain must NOT be called from RiskEstimator._get_principles_context")

    monkeypatch.setattr(store_module, "detect_domain", _boom)
    # Also guard against re-import inside the function body (the old code used a local import).
    sys.modules.pop("moralstack.constitution.store", None)
    monkeypatch.setattr(store_module, "detect_domain", _boom)

    est, _ = _make_estimator(["core"])
    # Should not raise.
    est._get_principles_context("How do I make a shiv?")


def test_get_principles_context_skips_runtime_domain_when_store_has_no_debug_info():
    """If get_debug_info is missing (legacy store), runtime_domain falls back to None — no crash."""

    class _NoDebugStore:
        def get_relevant_principles(self, query, top_k=10, domain=None):  # noqa: ARG002
            return []

        # intentionally no get_debug_info attribute

    est = LLMBasedRiskEstimator(policy=None, constitution_store=_NoDebugStore())
    result = est._get_principles_context("any prompt")
    assert result.runtime_domain is None


def test_get_principles_context_returns_empty_string_context_when_no_principles():
    """No principles + only-core prefiltered → ('', None) shape preserved."""
    est, _ = _make_estimator(["core"])
    result = est._get_principles_context("any prompt")
    assert result.formatted_context == ""
    assert result.runtime_domain is None


@pytest.mark.parametrize("prefiltered", [["core"], [], ["core", "legal"]])
def test_get_principles_context_never_returns_core_as_runtime_domain(prefiltered):
    """Invariant: runtime_domain is never the literal string 'core'."""
    est, _ = _make_estimator(prefiltered)
    result = est._get_principles_context("any prompt")
    assert result.runtime_domain != "core"


def test_get_principles_context_tolerates_new_keyword_only_store_params():
    """
    [Gap 1] The estimator must not force-pass a new keyword-only param the
    ConstitutionStoreProtocol declares optional (e.g. ``retrieval_phase``), so
    existing store doubles without it (like ``_FakeConstitutionStore`` above,
    which has no ``retrieval_phase``/``**kwargs``) keep working unchanged.
    """
    est, store = _make_estimator(["core"])
    result = est._get_principles_context("any prompt", retrieval_query="enriched query", retrieval_top_k=20)
    assert result.retrieval_succeeded is True
    assert len(store.get_relevant_principles_calls) == 1
    call = store.get_relevant_principles_calls[0]
    assert call["query"] == "enriched query"
    assert call["top_k"] == 20
    assert call["domain"] is None

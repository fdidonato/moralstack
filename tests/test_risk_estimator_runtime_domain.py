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
  - `_get_principles_context` calls `store.retrieve(domain=None)` only
  - it derives `runtime_domain` from the returned `PrincipleRetrievalResult.prefiltered_domains`
    (typed return channel — never the shared-attribute `get_debug_info()`)
  - `core` is excluded from the candidate set (retrieval-only pseudo-domain)
  - it never imports or calls `detect_domain`

Unify-constitution-retrieval-single-pass: `_get_principles_context` returns a
`_PrinciplesContextResult` (attribute access: `.formatted_context`,
`.runtime_domain`, plus the full retrieved `.principles` and retrieval-status flags)
instead of a plain 2-tuple, so it can carry the single upstream retrieval for reuse
by deliberation/critic/fast-path.

retrieval-request-scoped-state (this change, ai/plans/retrieval-request-scoped-state.md):
`_get_principles_context` now reads `store.retrieve(...)`'s typed return value instead of
`store.get_relevant_principles(...)` + `store.get_debug_info()` — the latter read a
shared, per-process instance attribute (`ConstitutionRetriever._last_debug_info`) that a
concurrent request could overwrite between another request's write and read (P0). A store
without `retrieve()` degrades loudly: a WARNING once per process, `runtime_domain=None`,
and `debug_snapshot["domain_channel"] = "fallback_no_retrieve"` — never a stale domain
(see `test_missing_retrieve_emits_warning_and_marker`, T9). `_FakeConstitutionStore` below
gained `retrieve()`; its assertions on `runtime_domain` are unchanged.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

import pytest

from moralstack.constitution.retrieval_result import PrincipleRetrievalResult
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from tests.fakes_constitution import GatedSharedDebugInfoStore


class _FakeConstitutionStore:
    """Minimal fake: returns canned principles via the typed retrieve() channel."""

    def __init__(self, prefiltered_domains: list[str]) -> None:
        self._prefiltered_domains = prefiltered_domains
        self.get_relevant_principles_calls: list[dict[str, Any]] = []

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list[Any]:
        self.get_relevant_principles_calls.append({"query": query, "top_k": top_k, "domain": domain})
        return []  # empty principles is fine — _get_principles_context still returns runtime_domain

    def retrieve(
        self,
        query: str,
        top_k: int = 10,
        domain: str | None = None,
        *,
        retrieval_phase: str = "risk_routing",
    ) -> PrincipleRetrievalResult:
        self.get_relevant_principles_calls.append({"query": query, "top_k": top_k, "domain": domain})
        return PrincipleRetrievalResult(
            principles=(),
            prefiltered_domains=tuple(self._prefiltered_domains),
            debug_info={"prefiltered_domains": list(self._prefiltered_domains)},
        )


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
    """If retrieve() is missing (legacy store), runtime_domain falls back to None — no crash."""

    class _NoDebugStore:
        def get_relevant_principles(self, query, top_k=10, domain=None):  # noqa: ARG002
            return []

        # intentionally no retrieve/get_debug_info attribute

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


def test_runtime_domain_does_not_leak_across_concurrent_retrieval_calls():
    """T1 (PRIMARY, decision channel) — ai/plans/retrieval-request-scoped-state.md.

    Thread A ("LEGAL" query) writes its own state and blocks BEFORE returning,
    inside the window the pre-fix estimator used to separate the principles
    fetch (get_relevant_principles) from the debug-info read (get_debug_info());
    thread B completes an entire _get_principles_context for another domain in
    between; A resumes. A must still get its OWN domain ("legal"), never B's
    ("medical"), which the shared `_last_debug_info` rebind would otherwise
    leak — the P0 bug this plan fixes. Today (before the fix) the estimator
    reads the legacy shared-attribute channel and this fails with
    ``AssertionError: 'medical' == 'legal'``.
    """
    store = GatedSharedDebugInfoStore()
    est = LLMBasedRiskEstimator(policy=None, constitution_store=store)

    result_a: dict[str, Any] = {}

    def thread_a() -> None:
        result_a["r"] = est._get_principles_context("A LEGAL question about a contract dispute")

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread A did not reach the gate"

    result_b = est._get_principles_context("a medical question about symptoms")

    store._release.set()
    t.join(timeout=10.0)
    assert not t.is_alive()

    assert result_b.runtime_domain == "medical"
    assert result_a["r"].runtime_domain == "legal"


class _CoreDirectionGatedStore:
    """Local double for the 'core in both directions' T1 regression variants
    (ai/plans/retrieval-request-scoped-state.md). A query containing "GATEME"
    gates (writes the legacy shared attribute, then blocks before returning —
    same shape as GatedSharedDebugInfoStore); a query containing "SPECIFIC"
    resolves to a regulated domain, everything else resolves to core-only."""

    def __init__(self) -> None:
        self._entered = threading.Event()
        self._release = threading.Event()

    def _own_domain(self, query: str) -> list[str]:
        return ["core", "legal"] if "SPECIFIC" in query else ["core"]

    def retrieve(
        self, query: str, top_k: int = 10, domain: str | None = None, *, retrieval_phase: str = "risk_routing"
    ) -> PrincipleRetrievalResult:
        own = self._own_domain(query)
        if "GATEME" in query:
            self._entered.set()
            assert self._release.wait(timeout=5.0), "release not signaled: broken test setup"
        return PrincipleRetrievalResult(
            principles=(), prefiltered_domains=tuple(own), debug_info={"prefiltered_domains": list(own)}
        )


def test_specific_request_keeps_own_domain_when_concurrent_general_request_writes_core_last():
    """Direction (a): A ("legal") gates; B (general/core-only) completes fully in
    between and writes ["core"] last; A must still resolve to its own "legal"."""
    store = _CoreDirectionGatedStore()
    est = LLMBasedRiskEstimator(policy=None, constitution_store=store)

    result_a: dict[str, Any] = {}

    def thread_a() -> None:
        result_a["r"] = est._get_principles_context("GATEME SPECIFIC a real legal question about contracts")

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread A did not reach the gate"

    result_b = est._get_principles_context("a general benign question")

    store._release.set()
    t.join(timeout=10.0)
    assert not t.is_alive()

    assert result_b.runtime_domain is None
    assert result_a["r"].runtime_domain == "legal"


def test_general_request_does_not_acquire_regulated_domain_from_concurrent_specific_request():
    """Direction (b), the insidious one (plan's own wording): B (general/core-only)
    gates; A ("legal") completes fully in between and writes ["core","legal"]
    last; B must stay None — a benign request must never ACQUIRE a regulated
    overlay by contamination (wrongly firing domain_regulated, §5.5)."""
    store = _CoreDirectionGatedStore()
    est = LLMBasedRiskEstimator(policy=None, constitution_store=store)

    result_b: dict[str, Any] = {}

    def thread_b() -> None:
        result_b["r"] = est._get_principles_context("GATEME a general benign question")

    t = threading.Thread(target=thread_b, name="thread-b")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread B did not reach the gate"

    result_a = est._get_principles_context("SPECIFIC a real legal question about contracts")

    store._release.set()
    t.join(timeout=10.0)
    assert not t.is_alive()

    assert result_a.runtime_domain == "legal"
    assert (
        result_b["r"].runtime_domain is None
    ), "general request must not acquire a domain from a concurrent specific request"


def test_missing_retrieve_emits_warning_and_marker(caplog):
    """T9 — the fallback is loud (ai/plans/retrieval-request-scoped-state.md).

    A store exposing only get_relevant_principles (no retrieve()) must degrade
    loudly: runtime_domain=None, a WARNING is emitted, and
    debug_snapshot["domain_channel"] == "fallback_no_retrieve". Never falls
    back to a stale/foreign domain via the removed get_debug_info() channel.
    """

    class _RetrieveLessConstitutionStore:
        def get_relevant_principles(self, query, top_k=10, domain=None):  # noqa: ARG002
            return []

    est = LLMBasedRiskEstimator(policy=None, constitution_store=_RetrieveLessConstitutionStore())
    # The warning is emitted by the shared helper in the leaf module, not by the
    # estimator's own logger: anchor on the emitting logger and on the message,
    # so this cannot pass on an unrelated WARNING from anywhere else.
    warn_logger = "moralstack.constitution.retrieval_result"
    with caplog.at_level("WARNING", logger=warn_logger):
        result = est._get_principles_context("any prompt")

    assert result.runtime_domain is None
    assert result.debug_snapshot.get("domain_channel") == "fallback_no_retrieve"
    matching = [
        r
        for r in caplog.records
        if r.levelname == "WARNING" and r.name == warn_logger and "has no retrieve()" in r.getMessage()
    ]
    seen = [(r.name, r.getMessage()) for r in caplog.records]
    assert matching, f"expected a WARNING from {warn_logger} naming the missing retrieve(); got {seen}"


def test_retrieve_path_marks_domain_channel_retrieve():
    """T9 (normal-path half) — the marker must also be pinned on the healthy
    path so 'retrieve' and 'fallback_no_retrieve' are distinguishable in the
    audit trail, not just the degraded state."""
    est, _ = _make_estimator(["core", "legal"])
    result = est._get_principles_context("a real legal question")
    assert result.debug_snapshot.get("domain_channel") == "retrieve"

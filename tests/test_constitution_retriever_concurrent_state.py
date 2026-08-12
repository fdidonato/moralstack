"""Concurrency/aliasing tests driving the REAL ConstitutionRetriever directly.

ai/plans/retrieval-request-scoped-state.md — T3, T4, T6, T8. T1/T2 (in
tests/test_risk_estimator_runtime_domain.py and
tests/test_deliberation_runner_concurrent_retrieval.py) exercise the fix
through the estimator/runner callers with a double; these tests close the gap
they leave open: a fix applied only to the callers, leaving shared per-request
state alive on ``ConstitutionRetriever``/``DomainPrefilter`` themselves, would
still pass T1/T2 but must fail here.
"""

from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

from moralstack.constitution.retriever import (
    AgentResult as _AgentResult,
)
from moralstack.constitution.retriever import (
    ConstitutionRetriever,
    ConstitutionRetrieverConfig,
    DomainPrefilter,
    EnhancedDomainAgent,
)
from moralstack.constitution.schema import Overlay, Principle

_NO_MATCH_AGENT_RESULT: dict = {
    "domain_match": False,
    "confidence": 0.0,
    "principle_ids": [],
    "reasoning": "n/a",
}


class _FakeProvider:
    """Minimal ConstitutionDataProvider (retriever.py Protocol) — two overlays."""

    def load_core(self) -> list[Principle]:
        return [Principle(id="CORE.1", level="hard", priority=100, title="core", rule="core rule")]

    def load_overlay(self, domain: str) -> Overlay:
        return Overlay(
            domain=domain,
            additional_principles=[
                Principle(id=f"{domain.upper()}.1", level="soft", priority=50, title=domain, rule=f"{domain} rule")
            ],
            keywords=[],
            description="",
        )

    def _get_available_domains(self) -> list[str]:
        return ["legal", "medical"]

    def get_domain_keywords(self) -> dict[str, list[str]]:
        return {"legal": ["law"], "medical": ["symptom"]}

    def get_domain_descriptions(self) -> dict[str, str]:
        return {"legal": "legal domain", "medical": "medical domain"}


def _make_retriever() -> ConstitutionRetriever:
    return ConstitutionRetriever(
        config=ConstitutionRetrieverConfig(use_enhanced_retrieval=True, use_domain_prefilter=True, max_parallel_agents=4),
        data_provider=_FakeProvider(),
    )


def test_retrieve_keeps_own_domains_under_deterministic_interleave() -> None:
    """T3 — real ConstitutionRetriever, deterministic interleave.

    Thread A ("LEGAL" query) gates INSIDE its own enhanced-agent evaluation
    (after the local `debug` dict for A's own domain already exists); thread B
    completes an entire retrieve() call for another domain while A is blocked;
    A resumes. A's returned prefiltered_domains must still be its own.
    """
    retriever = _make_retriever()
    entered = threading.Event()
    release = threading.Event()

    def _fake_prefilter_call(self, prompt, *, system_prompt, retrieval_phase="risk_routing"):  # noqa: ARG001
        if "LEGAL" in prompt:
            return {"domains": ["legal"], "confidence": 0.9}
        return {"domains": ["medical"], "confidence": 0.9}

    def _fake_agent_evaluate(self, query, *, retrieval_phase="risk_routing"):  # noqa: ARG001
        if self.domain_name == "legal":
            entered.set()
            assert release.wait(timeout=5.0), "release not signaled: broken test setup"
        return _AgentResult(principle_ids=[f"{self.domain_name.upper()}.1"], confidence=1.0, domain_match=True)

    with (
        patch.object(DomainPrefilter, "_call_openai", autospec=True, side_effect=_fake_prefilter_call),
        patch.object(EnhancedDomainAgent, "evaluate", autospec=True, side_effect=_fake_agent_evaluate),
    ):
        result_a: dict = {}

        def thread_a() -> None:
            result_a["r"] = retriever.retrieve("A LEGAL question about a contract dispute", domain=None)

        t = threading.Thread(target=thread_a, name="thread-a")
        t.start()
        assert entered.wait(timeout=5.0), "thread A did not reach the gate"

        result_b = retriever.retrieve("a medical question about symptoms today", domain=None)

        release.set()
        t.join(timeout=10.0)
        assert not t.is_alive()

    assert result_b.prefiltered_domains == ("core", "medical")
    assert result_a["r"].prefiltered_domains == ("core", "legal")


def test_prefilter_cache_entry_not_mutated_by_caller_domain() -> None:
    """T4 — cache poisoning (deterministic, no threads).

    retrieve(Q, domain=None) -> retrieve(Q, domain="medical") -> retrieve(Q,
    domain=None): the third call's prefiltered_domains must not contain
    "medical". Reproducible single-threaded — not a race
    (DomainPrefilter._cache alias + in-place domain append at the old
    retriever.py:1202-1203, closed by copy-on-return + a local copy at the
    ConstitutionRetriever.retrieve() call site).

    Before-evidence captured against the CURRENT (pre-fix) API
    (get_relevant_principles(domain=...) + get_debug_info()["prefiltered_domains"])
    is recorded in the implementer's report for this change: call 3 wrongly
    inherited "medical" and the raw cache entry itself held ["core", "medical"].
    """
    retriever = _make_retriever()
    query = "a long enough neutral query unrelated to any domain in particular"

    with (
        patch.object(DomainPrefilter, "_call_openai", return_value={"domains": [], "confidence": 0.0}),
        patch.object(EnhancedDomainAgent, "_call_openai", return_value=dict(_NO_MATCH_AGENT_RESULT)),
    ):
        r1 = retriever.retrieve(query, domain=None)
        r2 = retriever.retrieve(query, domain="medical")
        r3 = retriever.retrieve(query, domain=None)

    assert r1.prefiltered_domains == ("core",)
    assert r2.prefiltered_domains == ("core", "medical")
    assert r3.prefiltered_domains == ("core",), "call 3 must not inherit call 2's forced domain"

    assert retriever._domain_prefilter is not None
    cache_entries = list(retriever._domain_prefilter._cache.values())
    assert cache_entries == [["core"]], f"the cache entry itself must never contain the forced domain: {cache_entries}"


def test_returned_domains_do_not_alias_cache() -> None:
    """T8 — mutating the returned debug_info list must never mutate the
    prefilter's cache entry (closes the shallow-copy facet, plan assumption 5:
    a shallow ``dict(...)``/``.copy()`` downstream would otherwise still alias
    the cached list, letting a later in-place domain append retroactively
    mutate an already-persisted earlier request's metadata)."""
    retriever = _make_retriever()
    query = "a long enough neutral query, again unrelated to any domain here"

    with (
        patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["legal"], "confidence": 0.9}),
        patch.object(EnhancedDomainAgent, "_call_openai", return_value=dict(_NO_MATCH_AGENT_RESULT)),
    ):
        result = retriever.retrieve(query, domain=None)

    # prefiltered_domains is already an immutable tuple; debug_info still
    # carries the mutable list form for audit-shape compatibility — mutate that.
    mutable = result.debug_info["prefiltered_domains"]
    mutable.append("INJECTED")

    assert retriever._domain_prefilter is not None
    cache_entries = list(retriever._domain_prefilter._cache.values())
    assert all("INJECTED" not in entry for entry in cache_entries), cache_entries


def test_retriever_has_no_request_scoped_attributes() -> None:
    """T6 — structural guard: no shared per-request debug state survives a
    retrieve() call. Cheap; blocks reintroduction of the removed channel."""
    assert not hasattr(ConstitutionRetriever, "get_debug_info")

    retriever = _make_retriever()
    with (
        patch.object(DomainPrefilter, "_call_openai", return_value={"domains": [], "confidence": 0.0}),
        patch.object(EnhancedDomainAgent, "_call_openai", return_value=dict(_NO_MATCH_AGENT_RESULT)),
    ):
        retriever.retrieve("a long enough neutral query about nothing in particular", domain=None)

    assert not hasattr(retriever, "_last_debug_info")
    assert retriever._domain_prefilter is not None
    assert not hasattr(retriever._domain_prefilter, "_last_cache_lookup_hit")
    assert not hasattr(retriever._domain_prefilter, "_last_keywords_changed")


def test_retrieve_stamps_domain_channel_on_every_exit_path() -> None:
    """Required test 3 (retrieval-request-scoped-state-fix-handoff.md).

    ``domain_channel="retrieve"`` must be present in ``debug_info`` on all
    four ``ConstitutionRetriever.retrieve()`` exit paths: the empty-query
    path (which returns before the local ``debug`` dict is built, so it
    needs its own dict — its ``debug_info`` must be EXACTLY
    ``{"domain_channel": "retrieve"}``, plan §6 point 5), the two no-agents
    fallbacks (``fallback: True`` must still be present alongside), and the
    normal path.
    """
    retriever = _make_retriever()

    # 1. Empty query.
    empty_result = retriever.retrieve("", domain=None)
    assert empty_result.debug_info == {"domain_channel": "retrieve"}

    # 2. Enhanced no-agents fallback (use_enhanced_retrieval=True, the
    #    default of _make_retriever()).
    with (
        patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["legal"], "confidence": 0.9}),
        patch.object(ConstitutionRetriever, "_create_enhanced_agents", return_value=[]),
    ):
        enhanced_fallback = retriever.retrieve("a query about something", domain=None)
    assert enhanced_fallback.debug_info["domain_channel"] == "retrieve"
    assert enhanced_fallback.debug_info["fallback"] is True

    # 3. Legacy no-agents fallback (use_enhanced_retrieval=False).
    legacy_retriever = ConstitutionRetriever(
        config=ConstitutionRetrieverConfig(use_enhanced_retrieval=False, use_domain_prefilter=False, max_parallel_agents=4),
        data_provider=_FakeProvider(),
    )
    with patch.object(ConstitutionRetriever, "_create_domain_agents", return_value=[]):
        legacy_fallback = legacy_retriever.retrieve("a query about something", domain=None)
    assert legacy_fallback.debug_info["domain_channel"] == "retrieve"
    assert legacy_fallback.debug_info["fallback"] is True

    # 4. Normal path.
    with (
        patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["legal"], "confidence": 0.9}),
        patch.object(EnhancedDomainAgent, "_call_openai", return_value=dict(_NO_MATCH_AGENT_RESULT)),
    ):
        normal_result = retriever.retrieve("a normal query about legal matters", domain=None)
    assert normal_result.debug_info["domain_channel"] == "retrieve"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

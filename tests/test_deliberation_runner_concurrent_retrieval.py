"""T2 — second reader concurrency test, ai/plans/retrieval-request-scoped-state.md.

Targets ``DeliberationRunner._try_build_request_analysis_context``
(``deliberation_runner.py:462-516``), read at the retrieval-debug site and
persisted into ``retrieval_metadata``. Uses the SAME dual-channel double as T1
(``tests/test_risk_estimator_runtime_domain.py``). Necessary in addition to
T1: the two readers (risk estimator, deliberation runner) are independent, so
a fix applied only to the estimator would leave this reader's persisted audit
trail (``retrieval_metadata`` / the ``REQUEST_ANALYSIS_CONTEXT`` trace)
corrupted.

Also covers the two defects from the Codex diff review that blocked the first
implementation round (ai/reviews/codex-diff-review-retrieval-request-scoped-state-20260812-124500.md,
ai/handoffs/retrieval-request-scoped-state-fix-handoff.md):
- required test 1 — the runner's guarded legacy fallback must retrieve with
  the ENRICHED query, never the raw prompt (fail-open otherwise);
- required test 2 — the persisted ``domain_channel`` marker must come from
  the REAL runner path, not from a hand-built ``RequestAnalysisContext`` that
  supplies the very value it then asserts.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import MagicMock

from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import (
    DeliberationDependencies,
    OrchestratorConfig,
    ProcessedRequest,
)
from tests.fakes_constitution import (
    GatedSharedDebugInfoStore,
    RetrieveLessPrincipleStore,
    RetrieveNoMarkerStore,
)


def _make_runner(store: Any) -> DeliberationRunner:
    deps = DeliberationDependencies(
        policy=None,
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=MagicMock(),
    )
    cfg = OrchestratorConfig(
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=False,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
    )
    return DeliberationRunner(cfg, deps, protected_system_prompt="sys", logger=None, assembler=MagicMock())


def test_request_analysis_retrieval_metadata_does_not_leak_across_concurrent_calls() -> None:
    """Thread A ("LEGAL" prompt) gates inside the shared double's _compute, after
    the legacy shared attribute has been rebound but before returning; thread B
    completes an entire _try_build_request_analysis_context call for another
    domain in between; A resumes. A's persisted retrieval_metadata must carry
    its OWN prefiltered_domains, never B's."""
    store = GatedSharedDebugInfoStore()
    runner = _make_runner(store)

    result_a: dict[str, Any] = {}

    def thread_a() -> None:
        req_a = ProcessedRequest(request_id="req-a", prompt="A LEGAL question about a contract dispute")
        result_a["r"] = runner._try_build_request_analysis_context(req_a)

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert store._entered.wait(timeout=5.0), "thread A did not reach the gate"

    req_b = ProcessedRequest(request_id="req-b", prompt="a medical question about symptoms")
    result_b = runner._try_build_request_analysis_context(req_b)

    store._release.set()
    t.join(timeout=10.0)
    assert not t.is_alive()

    assert result_b is not None
    assert result_b.retrieval_metadata["prefiltered_domains"] == ["core", "medical"]

    assert result_a["r"] is not None
    assert result_a["r"].retrieval_metadata["prefiltered_domains"] == ["core", "legal"]


def test_legacy_fallback_retrieves_with_enriched_query_not_raw_prompt() -> None:
    """Required test 1 (retrieval-request-scoped-state-fix-handoff.md).

    A store exposing ``get_relevant_principles`` but NOT ``retrieve()`` must
    still make the runner return a ``RequestAnalysisContext`` — never
    ``None``, since a missing context flips ``use_precomputed`` to False and
    pushes the critic onto ``critique_with_relevant_principles``, whose
    retrieval query is the raw prompt (``critic_module.py:773``), instead of
    the enriched one (fail-open, deliberation_runner.py:2954-2955). The part
    that actually pins the fail-open: the query the store received must be
    the ENRICHED one (developer contract + prompt), not the raw prompt —
    asserted on the recorded query substring, not merely "context is not
    None".
    """
    store = RetrieveLessPrincipleStore(principles=("PRINCIPLE-1",))
    runner = _make_runner(store)

    contract = DeveloperContract.from_text("You are a customs broker assistant. Only discuss import tariffs.")
    request = ProcessedRequest(
        request_id="req-legacy",
        prompt="what's the rate?",
        developer_contract=contract,
    )

    result = runner._try_build_request_analysis_context(request)

    assert result is not None, "a legacy store without retrieve() must still yield a RequestAnalysisContext"
    assert result.retrieval_metadata["domain_channel"] == "fallback_no_retrieve"
    assert list(result.relevant_principles) == ["PRINCIPLE-1"]

    assert len(store.calls) == 1, "the fallback must call get_relevant_principles exactly once"
    received_query = store.calls[0]["query"]
    assert received_query != request.prompt, "the fallback must not degrade to the raw prompt"
    assert "customs broker" in received_query, "the enriched query must carry the developer contract text"
    assert request.prompt in received_query, "the enriched query still includes the user prompt"


def test_request_analysis_context_persists_domain_channel_marker(monkeypatch) -> None:
    """Required test 2 (retrieval-request-scoped-state-fix-handoff.md).

    Drives the REAL path ``_try_build_request_analysis_context`` ->
    ``_emit_request_analysis_context_finalize`` — replacing the earlier
    hand-built variant the Codex diff review flagged as tautological (it
    constructed ``RequestAnalysisContext`` by hand with the exact
    ``retrieval_metadata`` it then asserted on, so it could not fail on the
    defect: the runner never stamping ``domain_channel`` on its own
    retrieval path). Pins both the normal-path value (``"retrieve"``, via the
    runner's ``setdefault`` — ``RetrieveNoMarkerStore.retrieve()`` returns NO
    ``domain_channel`` key at all) and the fallback value
    (``"fallback_no_retrieve"``, via ``RetrieveLessPrincipleStore``, which has
    no ``retrieve()`` attribute)."""
    captured: list[Any] = []
    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.append_decision_trace",
        lambda dt: captured.append(dt),
    )

    class _RiskProto:
        score = 0.5

    for expected_channel, store in (
        ("retrieve", RetrieveNoMarkerStore()),
        ("fallback_no_retrieve", RetrieveLessPrincipleStore()),
    ):
        captured.clear()
        runner = _make_runner(store)
        request = ProcessedRequest(request_id="req-domain-channel", prompt="a real legal question about contracts")

        request_analysis = runner._try_build_request_analysis_context(request)
        assert request_analysis is not None, f"expected a RequestAnalysisContext for {expected_channel!r}"

        runner._emit_request_analysis_context_finalize(
            request_id="req-domain-channel",
            request_analysis=request_analysis,
            risk_estimation=_RiskProto(),
        )
        assert len(captured) == 1, f"expected exactly one persisted trace for {expected_channel!r}"
        assert captured[0].stage_payload["domain_channel"] == expected_channel

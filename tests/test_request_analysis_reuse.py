"""Request-scoped principle retrieval reuse (RequestAnalysisContext) — observability and call-count."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.types import (
    DeliberationDependencies,
    OrchestratorConfig,
    ProcessedRequest,
    RequestAnalysisContext,
)
from moralstack.reports.runtime_decisions import (
    build_cycle_cards,
    build_execution_strategy,
    build_runtime_decision_observability,
)


@dataclass
class _P:
    id: str
    title: str = "t"
    level: str = "soft"


def test_request_analysis_context_type_is_frozen():
    ctx = RequestAnalysisContext(
        relevant_principles=(_P("a"),),
        constitution=object(),
        detected_domain="general",
        retrieval_metadata={},
        retrieval_count=1,
        retrieval_duration_ms=1.0,
        retrieval_started_at_ms=0,
        retrieval_top_k=20,
    )
    assert ctx.retrieval_count == 1
    with pytest.raises(AttributeError):
        ctx.retrieval_count = 2  # type: ignore[misc]


def test_build_execution_strategy_prefers_last_request_analysis_trace():
    traces = [
        {
            "stage": "REQUEST_ANALYSIS_CONTEXT",
            "trace_json": json.dumps(
                {
                    "stage_payload": {
                        "retrieval_count": 1,
                        "constitution_domain": "x",
                        "reuse_targets": [],
                        "reuse_count": 0,
                    }
                }
            ),
        },
        {
            "stage": "REQUEST_ANALYSIS_CONTEXT",
            "trace_json": json.dumps(
                {
                    "stage_payload": {
                        "retrieval_count": 3,
                        "constitution_domain": "general",
                        "reuse_targets": ["critic"],
                        "reuse_count": 1,
                        "request_scoped": True,
                    }
                }
            ),
        },
    ]
    es = build_execution_strategy(
        traces,
        orchestration_events=[
            {"event_type": "RELEVANT_PRINCIPLES_RETRIEVED", "payload_json": "{}"},
            {"event_type": "RELEVANT_PRINCIPLES_REUSED", "payload_json": "{}"},
        ],
    )
    rac = es.get("request_analysis_context") or {}
    assert rac.get("relevant_principles_count") == 3
    assert rac.get("constitution_domain") == "general"
    assert rac.get("reuse_targets") == ["critic"]
    rpe = es.get("relevant_principles_events") or {}
    assert rpe.get("retrieved_count") == 1
    assert rpe.get("reused_count") == 1


def test_build_cycle_cards_notes_principles_reuse_per_cycle():
    traces = [{"stage": "CYCLE_SUMMARY", "trace_json": json.dumps({"stage_payload": {"cycle": 1}})}]
    orch = [
        {
            "event_type": "RELEVANT_PRINCIPLES_REUSED",
            "payload_json": json.dumps({"reuse_target": "critic", "cycle": 1}),
        }
    ]
    cards = build_cycle_cards(traces, orch)
    assert len(cards) == 1
    assert cards[0].get("principles_source_note") == "request-scoped reuse"


def test_single_store_retrieval_for_deliberation_critic_path(monkeypatch):
    """get_relevant_principles called once on the store when critic uses precomputed principles."""
    calls: list[str] = []

    class _Const:
        principles = [_P("p1")]
        active_overlay = None

    class _Store:
        def get_relevant_principles(
            self,
            query: str,
            top_k: int = 10,
            domain: Any = None,
            *,
            retrieval_phase: str = "risk_routing",
        ) -> list[_P]:
            calls.append("get_relevant_principles")
            return [_P("p1")]

        def get_constitution(self, domain: Any = None) -> Any:
            return _Const()

        def get_debug_info(self) -> dict[str, Any]:
            return {"prefilter_cache_status": "miss"}

    store = _Store()
    critic = MagicMock()
    critic.config = MagicMock()
    critic.config.top_k_principles = 20
    critic.critique = MagicMock(
        return_value=MagicMock(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
            raw_response="{}",
            parse_attempts=1,
            prompt="",
            system_prompt="",
        )
    )
    critic.critique_with_relevant_principles = MagicMock(side_effect=AssertionError("should not re-retrieve"))
    deps = DeliberationDependencies(
        policy=None,
        critic=critic,
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
    runner = DeliberationRunner(
        cfg,
        deps,
        protected_system_prompt="sys",
        logger=None,
        assembler=MagicMock(),
    )
    req = ProcessedRequest(request_id="req-ra", prompt="hello")

    class _RiskProto:
        score = 0.5
        risk_category = MagicMock(value="benign")
        detected_language = "en"
        intent_type = ""
        actionability_risk = MagicMock(value="LOW")
        detected_domain = None
        rationale = ""
        operational_risk = MagicMock(value="NONE")
        raw_response = ""
        used_fallback_parse = False
        risk_policy_action = MagicMock(value="ALLOW")
        harm_type = ""

    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.get_constitution_safe",
        lambda _store, _domain: _Const(),
    )

    state, _, _ = runner.run_deliberative_path(
        req,
        _RiskProto(),
        time.time(),
        constitution=None,
    )

    assert len(calls) == 1
    assert state.cycle >= 1
    critic.critique.assert_called()
    critic.critique_with_relevant_principles.assert_not_called()


def test_run_deliberative_path_with_supplied_request_analysis_makes_no_store_call(monkeypatch):
    """
    [unify-constitution-retrieval-single-pass, plan item 16] When the controller
    supplies a `request_analysis` (risk-owned single wave), `run_deliberative_path`
    must NOT call `_try_build_request_analysis_context` (i.e. no store call at all
    on the runner side) — the supplied context is authoritative even though it is
    not empty here.
    """
    calls: list[str] = []

    class _Const:
        principles = [_P("p1")]
        active_overlay = None

    class _Store:
        def get_relevant_principles(self, query: str, top_k: int = 10, domain: Any = None, **_kw: Any) -> list[_P]:
            calls.append("get_relevant_principles")
            return [_P("p1")]

        def get_constitution(self, domain: Any = None) -> Any:
            return _Const()

        def get_debug_info(self) -> dict[str, Any]:
            return {"prefilter_cache_status": "miss"}

    store = _Store()
    critic = MagicMock()
    critic.config = MagicMock()
    critic.config.top_k_principles = 20
    critic.critique = MagicMock(
        return_value=MagicMock(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            violated_hard=False,
            decision="PROCEED",
            revision_guidance="",
            raw_response="{}",
            parse_attempts=1,
            prompt="",
            system_prompt="",
        )
    )
    critic.critique_with_relevant_principles = MagicMock(side_effect=AssertionError("should not re-retrieve"))
    deps = DeliberationDependencies(
        policy=None,
        critic=critic,
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
    runner = DeliberationRunner(
        cfg,
        deps,
        protected_system_prompt="sys",
        logger=None,
        assembler=MagicMock(),
    )
    req = ProcessedRequest(request_id="req-ra-supplied", prompt="hello")

    class _RiskProto:
        score = 0.5
        risk_category = MagicMock(value="benign")
        detected_language = "en"
        intent_type = ""
        actionability_risk = MagicMock(value="LOW")
        detected_domain = None
        rationale = ""
        operational_risk = MagicMock(value="NONE")
        raw_response = ""
        used_fallback_parse = False
        risk_policy_action = MagicMock(value="ALLOW")
        harm_type = ""

    monkeypatch.setattr(
        "moralstack.orchestration.deliberation_runner.get_constitution_safe",
        lambda _store, _domain: _Const(),
    )

    supplied = RequestAnalysisContext(
        relevant_principles=(_P("supplied-1"),),
        constitution=_Const(),
        detected_domain=None,
        retrieval_count=1,
        retrieval_top_k=20,
    )

    state, _, _ = runner.run_deliberative_path(
        req,
        _RiskProto(),
        time.time(),
        constitution=None,
        request_analysis=supplied,
    )

    assert calls == [], "runner must not call the store when a request_analysis is supplied"
    assert state.cycle >= 1
    critic.critique.assert_called()
    critic.critique_with_relevant_principles.assert_not_called()
    # The critic saw the SUPPLIED principles, not a freshly-retrieved set.
    _, kwargs = critic.critique.call_args
    assert [p.id for p in kwargs["principles"]] == ["supplied-1"]


def test_build_runtime_decision_observability_includes_relevant_principles_events():
    vm = build_runtime_decision_observability(
        traces=[
            {
                "stage": "REQUEST_ANALYSIS_CONTEXT",
                "trace_json": json.dumps(
                    {
                        "stage_payload": {
                            "retrieval_count": 2,
                            "reuse_targets": ["critic"],
                            "reuse_count": 1,
                        }
                    }
                ),
            }
        ],
        orchestration_events=[
            {"event_type": "RELEVANT_PRINCIPLES_RETRIEVED", "payload_json": "{}"},
            {"event_type": "RELEVANT_PRINCIPLES_REUSED", "payload_json": "{}"},
        ],
        llm_calls=[],
    )
    es = vm["execution_strategy"]
    assert (es.get("relevant_principles_events") or {}).get("retrieved_count") == 1

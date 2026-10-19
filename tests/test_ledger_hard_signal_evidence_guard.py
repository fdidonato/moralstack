"""
PROJECT_SPEC §5.3 at the ledger apply point (H4): a cached non-REFUSE decision is never
applied to a request that carries hard-signal evidence.

Before the guard, ``is_safe_to_apply`` ignored the current request's risk evidence: a
cached NORMAL/SAFE hit on a ``fast_path`` request with e.g. ``Q9:cyber_malware`` was
forced to the benign route, so the only HARD check of a freshly generated draft
(``quick_check``) never ran. Contract pinned here: the blocked hit continues exactly as a
cache miss on its current route and emits ``LEDGER_FAST_PATH_NOT_APPLIED`` with
``gate_reason`` ``hard_signal_evidence`` (or ``hard_signal_refuse``). A cached REFUSE is
never blocked.

Offline and deterministic: ``decide_action``, ``apply_safe_complete_gating`` and
``get_route`` are patched so the route is chosen by the test; the ledger is real
(fixed-vector embedder, in-memory storage) except in the variants that need a
posture-ignoring ledger, which use a hit-always double.

Helpers import the new symbol inside the test bodies so every test fails on behavior (not
on collection) against the unguarded code.
"""

from __future__ import annotations

import json
from contextlib import ExitStack
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk.categories import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.models.risk.estimator import LLMBasedRiskEstimator
from moralstack.models.risk.schema import RiskEstimation, RiskEstimatorConfig
from moralstack.models.risk.signals.registry import registry as signal_registry
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.conversational_fast_path import ConversationalFastPathRunner
from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.delivery import GOVERNED_REFUSAL_SOURCE, finalize_delivery
from moralstack.orchestration.ledger import CachedDecision, LedgerResult, SemanticDecisionLedger
from moralstack.orchestration.ledger_storage import InMemoryLedgerStorage
from moralstack.orchestration.path_router import _HARD_SEMANTIC_SIGNALS
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest, RiskThresholds
from moralstack.utils.output_protection import create_protector
from tests.test_fast_path_single_retrieval import (
    _INTENT_BENIGN_JSON,
    _CountingStore,
    _GenResult,
    _MiniPolicy,
    _Principle,
    _quick_check_result,
)

PROMPT = "How does the thing described in this prompt work?"
NORMAL = "NORMAL_COMPLETE"
SAFE = "SAFE_COMPLETE"
REFUSE = "REFUSE"
HARD_EVIDENCE = "hard_signal_evidence"
HARD_REFUSE = "hard_signal_refuse"
DELIBERATION_GATE = "current_route_requires_deliberation"


# =============================================================================
# Harness
# =============================================================================


class _StubEmbedder:
    """Fixed-vector embedder: deterministic ledger without a real embedding model."""

    def embed(self, text: str) -> list[float]:
        return [1.0, 0.0]


class _CapturingEventEmitter:
    """Minimal event emitter recording emit_orchestration_event invocations."""

    def __init__(self) -> None:
        self.orchestration_events: list[dict[str, Any]] = []

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        self.orchestration_events.append(kwargs)

    def emit_llm_call(self, **kwargs: Any) -> None:
        pass

    def emit_decision_trace(self, **kwargs: Any) -> None:
        pass

    def of_type(self, event_type: str) -> list[dict[str, Any]]:
        return [e for e in self.orchestration_events if e.get("event_type") == event_type]


class _RaisingOnNotAppliedEmitter(_CapturingEventEmitter):
    def __init__(self) -> None:
        super().__init__()
        self.raised = 0

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        if kwargs.get("event_type") == "LEDGER_FAST_PATH_NOT_APPLIED":
            self.raised += 1
            raise RuntimeError("emitter down")
        super().emit_orchestration_event(**kwargs)


class _EvidenceRiskEstimator:
    """Risk estimator double returning a fixed estimation."""

    def __init__(self, estimation: RiskEstimation) -> None:
        self._estimation = estimation

    def estimate(self, prompt: str, **_kw: Any) -> RiskEstimation:
        return self._estimation


class _HitAlwaysLedger:
    """Posture-ignoring ledger double: every lookup is a hit (defense-in-depth variants)."""

    def __init__(self, cached_action: str) -> None:
        self._cached = CachedDecision(final_action=cached_action, risk_score=0.1, governance_posture="NORMAL")

    def lookup(self, **_kw: Any) -> LedgerResult:
        return LedgerResult(
            is_hit=True, cached_decision=self._cached, similarity=0.99, from_turn=1, reason="", query_embedding=None
        )

    def store(self, **_kw: Any) -> bool:
        return False


class _SensitiveOverlayStore(_CountingStore):
    """Store whose active overlay is sensitive -> governance posture ELEVATED."""

    def get_constitution(self, domain: str | None = None) -> Any:
        overlay = SimpleNamespace(sensitive=True, excluded=False)
        return SimpleNamespace(principles=list(self._principles), active_overlay=overlay, constitution_corrupted=False)


def _est(
    *,
    signals: list[str] | None = None,
    category: RiskCategory = RiskCategory.BENIGN,
    score: float = 0.05,
    op_risk: OperationalRisk = OperationalRisk.NONE,
) -> RiskEstimation:
    return RiskEstimation(
        score=score,
        confidence=0.9,
        risk_category=category,
        operational_risk=op_risk,
        semantic_signals=list(signals or []),
        request_type="factual_query",
    )


def _evid_signal(signal: str = "Q5:physical_harm") -> RiskEstimation:
    return _est(signals=[signal])


def _evid_category() -> RiskEstimation:
    return _est(category=RiskCategory.CLEARLY_HARMFUL, score=0.95)


def _no_evid() -> RiskEstimation:
    return _est()


def _decision(
    final_action: str = NORMAL,
    path: str = "DELIBERATIVE_PATH",
    reason_codes: list[str] | None = None,
) -> Decision:
    return Decision(
        final_action=final_action,  # type: ignore[arg-type]
        path=path,  # type: ignore[arg-type]
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=list(reason_codes or []),
    )


_ROUTE_ACTION = {
    "fast_path": RiskPolicyAction.ALLOW,
    "benign": RiskPolicyAction.ALLOW,
    "safe_complete": RiskPolicyAction.ALLOW,
    "refuse": RiskPolicyAction.DENY,
    "deliberative": RiskPolicyAction.DELIBERATE,
}


@dataclass
class _Rig:
    controller: OrchestrationController
    critic: MagicMock
    policy: MagicMock
    emitter: _CapturingEventEmitter
    ledger: Any


def _rig(
    estimation: RiskEstimation,
    *,
    ledger: Any = None,
    store: _CountingStore | None = None,
    emitter: _CapturingEventEmitter | None = None,
    quick_check_passed: bool = True,
) -> _Rig:
    """Build a fresh controller + ledger per test (xdist-safe, no shared state)."""
    store = store or _CountingStore([_Principle("HARD.1", level="hard")])
    ledger = ledger if ledger is not None else SemanticDecisionLedger(_StubEmbedder(), InMemoryLedgerStorage())
    emitter = emitter or _CapturingEventEmitter()
    critic = MagicMock()
    critic.config = SimpleNamespace(top_k_principles=5)
    critic.quick_check.return_value = _quick_check_result(quick_check_passed)
    policy = MagicMock()
    policy.generate.return_value = _GenResult(text="A safe response.")
    config = OrchestratorConfig(
        risk_thresholds=RiskThresholds(),
        max_deliberation_cycles=1,
        timeout_ms=60_000,
        parallel_module_calls=False,
        enable_simulation=False,
        enable_perspectives=False,
        enable_hindsight=False,
    )
    controller = OrchestrationController(
        config=config,
        policy=policy,
        risk_estimator=_EvidenceRiskEstimator(estimation),
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=create_protector(),
        protected_system_prompt="You are a helpful assistant.",
        event_emitter=emitter,
        ledger=ledger,
    )
    return _Rig(controller, critic, policy, emitter, ledger)


def _seed(ledger: SemanticDecisionLedger, cached_action: str, *, posture: str = "NORMAL") -> None:
    """Store a turn-1 decision keyed exactly as the controller will look it up (turn 2)."""
    stored = ledger.store(
        prompt=PROMPT,
        contract_hash="",
        posture=posture,
        domain=None,
        decision=CachedDecision(final_action=cached_action, risk_score=0.1, governance_posture=posture),
        intent_clarity="HIGH",
        request_type="factual_query",
        turn_index=1,
    )
    assert stored is True


def _process(
    rig: _Rig,
    *,
    route: str,
    decision: Decision,
    request_id: str,
    conversation_id: str,
    extra_patches: list[Any] | None = None,
) -> Any:
    expl = DecisionExplanation(final_action=decision.final_action, risk_category="benign")
    with ExitStack() as stack:
        stack.enter_context(patch("moralstack.orchestration.controller.decide_action", return_value=(decision, expl)))
        stack.enter_context(
            patch(
                "moralstack.orchestration.controller.apply_safe_complete_gating",
                side_effect=lambda dec, *_a, **_k: dec,
            )
        )
        stack.enter_context(
            patch("moralstack.orchestration.controller.get_route", return_value=(route, False, _ROUTE_ACTION[route]))
        )
        for p in extra_patches or []:
            stack.enter_context(p)
        return rig.controller.process(
            ProcessedRequest(prompt=PROMPT, request_id=request_id), conversation_id=conversation_id, turn_index=2
        )


def _was_cached(result: Any) -> bool:
    summary = result.conversation_governance_state_out.turn_decisions_summary
    assert summary, "turn summary missing: cannot read was_cached"
    return bool(summary[-1].was_cached)


def _assert_blocked(rig: _Rig, result: Any, reason: str, *, cached_action: str, current_route: str) -> dict[str, Any]:
    """Common assertions when the guard fires: not applied, payload shape intact, was_cached False."""
    em = rig.emitter
    assert em.of_type("LEDGER_FAST_PATH_APPLIED") == []
    not_applied = em.of_type("LEDGER_FAST_PATH_NOT_APPLIED")
    assert len(not_applied) == 1
    ev = not_applied[0]
    assert ev["reason_codes"] == [reason]
    assert ev["decision"] == "rejected"
    payload = ev["payload"]
    assert payload["gate_reason"] == reason
    assert set(payload) == {
        "from_turn",
        "similarity",
        "cached_action",
        "current_action",
        "current_route",
        "gate_reason",
    }
    assert payload["cached_action"] == cached_action
    assert payload["current_route"] == current_route
    assert _was_cached(result) is False
    return ev


# =============================================================================
# G1-G8: the guard (RED at the parent commit)
# =============================================================================


@pytest.mark.parametrize("cached_action", [NORMAL, SAFE])
@pytest.mark.parametrize("signal", sorted(_HARD_SEMANTIC_SIGNALS))
def test_g1_fast_path_hard_signal_hit_is_not_applied(signal: str, cached_action: str) -> None:
    rig = _rig(_evid_signal(signal))
    _seed(rig.ledger, cached_action)
    result = _process(rig, route="fast_path", decision=_decision(), request_id="req-g1", conversation_id=f"conv-g1-{signal}")
    ev = _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=cached_action, current_route="fast_path")
    assert ev["payload"]["current_action"] == NORMAL
    rig.critic.quick_check.assert_called_once()
    assert result.response.metadata.final_action == NORMAL
    assert result.path_taken == "fast"


def test_g2_benign_route_hard_signal_hit_is_not_applied() -> None:
    rig = _rig(_evid_signal())
    _seed(rig.ledger, NORMAL)
    result = _process(
        rig,
        route="benign",
        decision=_decision(path="FAST_PATH"),
        request_id="req-g2",
        conversation_id="conv-g2",
    )
    _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=NORMAL, current_route="benign")
    assert result.response.metadata.final_action == NORMAL
    rig.policy.generate.assert_called_once()


@pytest.mark.parametrize("cached_action", [SAFE, NORMAL])
def test_g3_safe_complete_route_cached_never_loosens_current_safe(cached_action: str) -> None:
    # A real ledger cannot hit here (see plan Current behavior 10); posture-ignoring double.
    rig = _rig(_evid_signal(), ledger=_HitAlwaysLedger(cached_action))
    result = _process(
        rig,
        route="safe_complete",
        decision=_decision(SAFE, path="FAST_PATH"),
        request_id="req-g3",
        conversation_id=f"conv-g3-{cached_action}",
    )
    _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=cached_action, current_route="safe_complete")
    assert result.response.metadata.final_action == SAFE


@pytest.mark.parametrize("cached_action", [NORMAL, SAFE])
def test_g4_category_evidence_refuse_route_hit_is_not_applied(cached_action: str) -> None:
    """clearly_harmful category without a hard semantic signal and a REFUSE that is NOT a
    hard-signal refuse: posture stays NORMAL on a real ledger, so a posture-ignoring double
    is used. The guard, not the ledger, must keep the cached decision from being applied."""
    rig = _rig(_evid_category(), ledger=_HitAlwaysLedger(cached_action))
    result = _process(
        rig,
        route="refuse",
        decision=_decision(REFUSE, path="FAST_PATH", reason_codes=["policy_refuse"]),
        request_id="req-g4",
        conversation_id=f"conv-g4-{cached_action}",
    )
    _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=cached_action, current_route="refuse")
    assert result.response.metadata.final_action == REFUSE


def test_g5_hard_signal_refuse_without_evidence_is_blocked() -> None:
    """D5b: op_risk HIGH anchors the REFUSE (risk_clearly_harmful) but there is no hard
    topical evidence; the ledger double ignores the ESCALATED posture."""
    rig = _rig(_est(op_risk=OperationalRisk.HIGH, score=0.9), ledger=_HitAlwaysLedger(NORMAL))
    result = _process(
        rig,
        route="refuse",
        decision=_decision(REFUSE, path="FAST_PATH", reason_codes=["risk_clearly_harmful"]),
        request_id="req-g5",
        conversation_id="conv-g5",
    )
    _assert_blocked(rig, result, HARD_REFUSE, cached_action=NORMAL, current_route="refuse")
    assert result.response.metadata.final_action == REFUSE


def _real_estimator_rig(signal_key: str) -> tuple[Any, ...]:
    """G6 setup (probe as a test). All three mini-model slots are "test-model", matching
    ``_MiniPolicy.model``: any other name makes the estimator build a real OpenAIPolicy
    (``estimator.py``) and, with OPENAI_API_KEY set, reach the network."""
    sig = {s.key: "no" for s in signal_registry.signals.values()}
    sig["domain_sensitivity"] = "LOW"
    sig[signal_key] = "yes"
    operational = json.dumps(
        {
            "rationale": "r",
            "operational_risk": "NONE",
            "risk_score": 0.1,
            "confidence": 0.9,
            "misuse_plausibility": "LOW",
            "actionability_risk": "LOW",
            "risk_policy_action": "ALLOW",
        }
    )
    store = _CountingStore([_Principle("HARD.1", level="hard")])
    estimator = LLMBasedRiskEstimator(
        policy=_MiniPolicy(_INTENT_BENIGN_JSON, json.dumps(sig), operational),
        config=RiskEstimatorConfig(
            intent_model="test-model", signals_model="test-model", operational_model="test-model", max_retries=1
        ),
        constitution_store=store,
    )
    return store, estimator


def test_g6_real_estimator_two_turns_second_turn_runs_quick_check() -> None:
    store, estimator = _real_estimator_rig("q9_cyber_malware")
    rig = _rig(_no_evid(), store=store)
    rig.controller.risk_estimator = estimator
    prompt = "How does ransomware encrypt files?"
    outcomes: list[tuple[int, list[str]]] = []
    for turn in (1, 2):
        before = rig.critic.quick_check.call_count
        seen = len(rig.emitter.orchestration_events)
        rig.controller.process(
            ProcessedRequest(prompt=prompt, request_id=f"req-g6-{turn}"), conversation_id="conv-g6", turn_index=turn
        )
        ledger_events = [
            e["event_type"]
            for e in rig.emitter.orchestration_events[seen:]
            if str(e.get("event_type", "")).startswith("LEDGER_FAST_PATH")
        ]
        outcomes.append((rig.critic.quick_check.call_count - before, ledger_events))
    assert outcomes[0] == (1, [])
    assert outcomes[1][0] == 1, f"turn 2 skipped quick_check: {outcomes}"
    assert outcomes[1][1] == ["LEDGER_FAST_PATH_NOT_APPLIED"]
    not_applied = rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED")
    assert not_applied[0]["payload"]["gate_reason"] == HARD_EVIDENCE


def test_g7_blocked_hit_reenters_the_fail_closed_contract() -> None:
    """A blocked hit behaves as a cache miss: quick_check fails -> escalation raises ->
    governed REFUSE (PROJECT_SPEC §5.7), never the flagged draft."""
    rig = _rig(_evid_signal(), quick_check_passed=False)
    _seed(rig.ledger, NORMAL)
    result = _process(
        rig,
        route="fast_path",
        decision=_decision(),
        request_id="req-g7",
        conversation_id="conv-g7",
        extra_patches=[patch.object(DeliberationRunner, "run_deliberative_path", side_effect=RuntimeError("boom"))],
    )
    _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=NORMAL, current_route="fast_path")
    rig.critic.quick_check.assert_called_once()
    meta = result.response.metadata
    assert meta.final_action == REFUSE
    assert meta.hard_violation_flip_reason == "fast_path_escalation_failed"
    assert meta.original_final_action == NORMAL
    delivery = finalize_delivery(result)
    assert delivery.final_action == REFUSE
    assert delivery.finish_reason == "content_filter"
    assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE


def test_g8_elevated_posture_hit_is_blocked_independently_of_the_key() -> None:
    rig = _rig(_evid_signal(), store=_SensitiveOverlayStore([_Principle("HARD.1", level="hard")]))
    _seed(rig.ledger, NORMAL, posture="ELEVATED")
    result = _process(rig, route="fast_path", decision=_decision(), request_id="req-g8", conversation_id="conv-g8")
    _assert_blocked(rig, result, HARD_EVIDENCE, cached_action=NORMAL, current_route="fast_path")
    rig.critic.quick_check.assert_called_once()


# =============================================================================
# HLP: the pure helper
# =============================================================================


@pytest.mark.parametrize("signal", sorted(_HARD_SEMANTIC_SIGNALS))
def test_hlp_every_hard_signal_blocks_a_non_refuse_cached_action(signal: str) -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    for cached in (NORMAL, SAFE):
        assert (
            ledger_hard_signal_block_reason(
                cached_action=cached, risk_estimation=_evid_signal(signal), hard_signal_refuse=False
            )
            == HARD_EVIDENCE
        )


@pytest.mark.parametrize("hard_signal_refuse", [False, True])
@pytest.mark.parametrize("est", [_evid_signal(), _evid_category(), _no_evid()], ids=["signal", "category", "none"])
def test_hlp_cached_refuse_is_never_blocked(est: RiskEstimation, hard_signal_refuse: bool) -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    assert (
        ledger_hard_signal_block_reason(cached_action=REFUSE, risk_estimation=est, hard_signal_refuse=hard_signal_refuse)
        is None
    )


def test_hlp_clearly_harmful_category_enum_and_string() -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    enum_est = _evid_category()
    str_est = SimpleNamespace(semantic_signals=[], risk_category="clearly_harmful")
    for est in (enum_est, str_est):
        assert (
            ledger_hard_signal_block_reason(cached_action=NORMAL, risk_estimation=est, hard_signal_refuse=False)  # type: ignore[arg-type]
            == HARD_EVIDENCE
        )


def test_hlp_hard_signal_refuse_without_evidence_and_no_evidence_cases() -> None:
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    assert (
        ledger_hard_signal_block_reason(cached_action=NORMAL, risk_estimation=_no_evid(), hard_signal_refuse=True)
        == HARD_REFUSE
    )
    # Evidence wins over the refuse arm when both hold.
    assert (
        ledger_hard_signal_block_reason(cached_action=NORMAL, risk_estimation=_evid_signal(), hard_signal_refuse=True)
        == HARD_EVIDENCE
    )
    assert ledger_hard_signal_block_reason(cached_action=SAFE, risk_estimation=_no_evid(), hard_signal_refuse=False) is None
    # A bare operational_risk HIGH without a P0 signal is not hard evidence.
    bare_op = _est(op_risk=OperationalRisk.HIGH, score=0.9)
    assert ledger_hard_signal_block_reason(cached_action=NORMAL, risk_estimation=bare_op, hard_signal_refuse=False) is None


def test_hlp_magicmock_risk_is_not_evidence() -> None:
    """A2: ledger tests that wire ``risk_estimator=MagicMock()`` must not flip."""
    from moralstack.orchestration.conversational_fast_path import ledger_hard_signal_block_reason

    assert (
        ledger_hard_signal_block_reason(cached_action=NORMAL, risk_estimation=MagicMock(), hard_signal_refuse=False) is None
    )


# =============================================================================
# P1-P7: behavior that must not change (PIN, green before and after)
# =============================================================================


@pytest.mark.parametrize("route", ["fast_path", "benign"])
def test_p1_cached_refuse_with_hard_evidence_is_still_applied(route: str) -> None:
    rig = _rig(_evid_signal())
    _seed(rig.ledger, REFUSE)
    result = _process(
        rig,
        route=route,
        decision=_decision(path="FAST_PATH" if route == "benign" else "DELIBERATIVE_PATH"),
        request_id="req-p1",
        conversation_id=f"conv-p1-{route}",
    )
    applied = rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED")
    assert len(applied) == 1
    assert applied[0]["payload"]["cached_action"] == REFUSE
    assert applied[0]["payload"]["forced_route"] == "refuse"
    assert rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED") == []
    assert result.response.metadata.final_action == REFUSE


def test_p2_deliberative_route_keeps_the_existing_gate_reason_byte_identical() -> None:
    rig = _rig(_evid_signal())
    _seed(rig.ledger, NORMAL)
    _process(
        rig,
        route="deliberative",
        decision=_decision(),
        request_id="req-p2",
        conversation_id="conv-p2",
        extra_patches=[patch.object(DeliberationRunner, "run_deliberative_path", side_effect=RuntimeError("boom"))],
    )
    not_applied = rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED")
    assert len(not_applied) == 1
    ev = not_applied[0]
    assert ev["reason_codes"] == [DELIBERATION_GATE]
    assert ev["payload"]["gate_reason"] == DELIBERATION_GATE
    assert ev["payload"]["current_route"] == "deliberative"
    assert ev["payload"]["cached_action"] == NORMAL
    assert set(ev["payload"]) == {
        "from_turn",
        "similarity",
        "cached_action",
        "current_action",
        "current_route",
        "gate_reason",
    }


def test_p3_no_evidence_fast_path_hit_is_applied_and_skips_quick_check() -> None:
    """Records D9 as designed (non-hard fast_path hits still skip quick_check); control for G1."""
    rig = _rig(_no_evid())
    _seed(rig.ledger, NORMAL)
    result = _process(rig, route="fast_path", decision=_decision(), request_id="req-p3", conversation_id="conv-p3")
    applied = rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED")
    assert len(applied) == 1
    assert applied[0]["payload"]["forced_route"] == "benign"
    assert rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED") == []
    rig.critic.quick_check.assert_not_called()
    assert _was_cached(result) is True


@pytest.mark.parametrize(
    ("route", "cached_action", "forced_route", "current"),
    [
        ("benign", NORMAL, "benign", _decision(path="FAST_PATH")),
        ("safe_complete", SAFE, "safe_complete", _decision(SAFE, path="FAST_PATH")),
    ],
)
def test_p4_no_evidence_non_deliberative_hits_are_applied(
    route: str, cached_action: str, forced_route: str, current: Decision
) -> None:
    rig = _rig(_no_evid())
    _seed(rig.ledger, cached_action)
    _process(rig, route=route, decision=current, request_id="req-p4", conversation_id=f"conv-p4-{route}")
    applied = rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED")
    assert len(applied) == 1
    assert applied[0]["payload"]["forced_route"] == forced_route
    assert rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED") == []


def test_p5_ledger_miss_with_hard_evidence_emits_no_ledger_events() -> None:
    rig = _rig(_evid_signal())  # nothing seeded -> miss
    _process(rig, route="fast_path", decision=_decision(), request_id="req-p5", conversation_id="conv-p5")
    assert rig.emitter.of_type("LEDGER_FAST_PATH_APPLIED") == []
    assert rig.emitter.of_type("LEDGER_FAST_PATH_NOT_APPLIED") == []
    rig.critic.quick_check.assert_called_once()


def test_p6_emitter_failure_on_not_applied_does_not_break_the_request() -> None:
    """PROJECT_SPEC §5.6: observability is best-effort (PIN: holds before and after the guard)."""
    rig = _rig(_evid_signal(), emitter=_RaisingOnNotAppliedEmitter())
    _seed(rig.ledger, NORMAL)
    result = _process(rig, route="fast_path", decision=_decision(), request_id="req-p6", conversation_id="conv-p6")
    assert result.response.metadata.final_action == NORMAL


def test_p6b_guard_survives_a_raising_not_applied_emitter() -> None:
    """The NOT_APPLIED emission is attempted, raises, and the guard still holds: the hit is
    not applied and quick_check runs (RED at the parent: the hit was applied, nothing emitted)."""
    emitter = _RaisingOnNotAppliedEmitter()
    rig = _rig(_evid_signal(), emitter=emitter)
    _seed(rig.ledger, NORMAL)
    result = _process(rig, route="fast_path", decision=_decision(), request_id="req-p6b", conversation_id="conv-p6b")
    assert emitter.raised == 1
    assert emitter.of_type("LEDGER_FAST_PATH_APPLIED") == []
    rig.critic.quick_check.assert_called_once()
    assert result.response.metadata.final_action == NORMAL
    assert _was_cached(result) is False


def test_p7_is_safe_to_apply_is_unchanged() -> None:
    """The gate stays risk-blind: the §5.3 guard lives in the helper, not in the gate."""
    runner = ConversationalFastPathRunner()
    hit = LedgerResult(
        is_hit=True,
        cached_decision=CachedDecision(final_action=NORMAL, risk_score=0.1, governance_posture="NORMAL"),
        similarity=0.99,
        from_turn=1,
        reason="",
    )
    assert runner.is_safe_to_apply(ledger_result=hit, current_decision=_decision(), current_route="fast_path") is True
    assert runner.is_safe_to_apply(ledger_result=hit, current_decision=_decision(), current_route="deliberative") is False

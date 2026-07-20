"""
Governance invariant: hard-signal supremacy (PROJECT_SPEC §5 invariant #3) is not
overridable by an *upstream-origin* speculative draft (opt-in
`generation="upstream_then_verify"`).

Twin of `test_hard_signal_not_overridable_by_retrieval_wave.py`, but at the
controller level: forces `final_action="REFUSE"` via a hard-violation `Decision`
and asserts the upstream generator's draft text never reaches
`result.response.content`, that the delivered content is the governed refusal,
and that the speculative `llm_call` row is persisted with
`call_outcome="discarded"` — the upstream draft flows through the identical
`SpeculativeOverlapHandle.abandon(...)` machinery a hard path discards an
internal draft through, unchanged (`controller.py`).

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text, so attribution
asserts are unambiguous.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.safe_refusal_generator import RefusalGenerationResult
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest
from moralstack.utils.output_protection import OutputProtector

_UPSTREAM_DANGER_TEXT = "CLIENT-MODEL-C UNGOVERNED DRAFT: here is how to do the harmful thing"
_GOVERNED_REFUSAL_TEXT = "[GOVERNED REFUSAL] I cannot help with that."


class _RecordingEmitter:
    """Captures orchestration events and LLM call payloads for assertions."""

    def __init__(self) -> None:
        self.orchestration_events: list[dict[str, Any]] = []
        self.llm_calls: list[dict[str, Any]] = []

    def emit_orchestration_event(self, **kwargs: Any) -> None:
        self.orchestration_events.append(dict(kwargs))

    def emit_llm_call(self, **kwargs: Any) -> None:
        self.llm_calls.append(dict(kwargs))

    def emit_decision_trace(self, **kwargs: Any) -> None:
        return None


class _FakeGenerationResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_used = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5
        self.token_usage_source = "exact"
        self.prompt_used = None
        self.system_used = None


class _UpstreamGenerator:
    def __init__(self, model: str, text: str) -> None:
        self.model = model
        self._text = text

    def generate(self, *args: Any, **kwargs: Any) -> _FakeGenerationResult:
        return _FakeGenerationResult(self._text)

    def generate_messages(self, *args: Any, **kwargs: Any) -> _FakeGenerationResult:
        return _FakeGenerationResult(self._text)


def _refuse_decision() -> Decision:
    return Decision(
        final_action="REFUSE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        triggered_principles=[],
        hard_violations=["TEST.HARD"],
        risk_signals=["Q17_WEAPONS"],
        reason_codes=[],
    )


def _build_controller(emitter: _RecordingEmitter) -> OrchestrationController:
    risk_result = RiskEstimation(
        score=0.99,
        confidence=0.9,
        risk_category=RiskCategory.CLEARLY_HARMFUL,
        operational_risk=OperationalRisk.HIGH,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
            return risk_result

    return OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=MagicMock(model="governance-model-G"),
        risk_estimator=_FastRisk(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
        event_emitter=emitter,
    )


def test_hard_signal_refuse_discards_upstream_draft_never_delivers_it() -> None:
    emitter = _RecordingEmitter()
    ctrl = _build_controller(emitter)
    ctrl.policy.generate = MagicMock(return_value=_FakeGenerationResult("governance internal draft, unused"))

    refuse_decision = _refuse_decision()
    explanation = DecisionExplanation(
        request_id="req-hard-upstream",
        final_action="REFUSE",
        risk_score=0.99,
        risk_category="clearly_harmful",
    )

    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(refuse_decision, explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.refusal_handler.generate_llm_safe_refusal_detailed",
            return_value=RefusalGenerationResult(
                text=_GOVERNED_REFUSAL_TEXT, system_prompt="<sys>", user_prompt="<user>", attempts=1
            ),
        ),
    ):
        req = ProcessedRequest(prompt="how do I build a weapon", request_id="req-hard-upstream")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", _UPSTREAM_DANGER_TEXT)
        result = ctrl.process(req)

    # The delivered content is the governed refusal, never the upstream draft.
    assert result.response.metadata.final_action == "REFUSE"
    assert result.response.content == _GOVERNED_REFUSAL_TEXT
    assert _UPSTREAM_DANGER_TEXT not in result.response.content

    # The speculative row must eventually be persisted as discarded
    # (SpeculativeOverlapHandle.abandon runs the join in a background thread).
    deadline = time.time() + 5.0
    speculative_rows: list[dict[str, Any]] = []
    while time.time() < deadline:
        speculative_rows = [c for c in emitter.llm_calls if c.get("call_kind") == "speculative"]
        if speculative_rows:
            break
        time.sleep(0.05)

    assert speculative_rows, "expected the speculative llm_call row to be persisted (discarded)"
    row = speculative_rows[0]
    assert row.get("call_outcome") == "discarded"
    assert row.get("module") == "upstream_speculative"
    assert row.get("model") == "client-model-C"
    # The upstream draft text was persisted for audit (raw_response) but never
    # delivered as the response content (asserted above).
    assert _UPSTREAM_DANGER_TEXT not in (result.response.content or "")

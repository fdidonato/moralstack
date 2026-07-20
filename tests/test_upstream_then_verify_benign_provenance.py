"""
Benign-route provenance for `generation="upstream_then_verify"` (round-5 correction).

`run_benign_fast_path` output-protects the supplied speculative draft and
returns via `ResponseMetadata.from_decision` -- it does NOT emit a separate
reuse `llm_call` (unlike FAST_PATH / deliberative cycle-1 reuse, covered by
`test_upstream_then_verify_reused_draft_metadata.py`). Its upstream provenance
is carried by:
  (a) the FINAL metadata (`draft_origin`/`draft_model`), and
  (b) the *speculative* row itself, persisted by the handle as
      `module="upstream_speculative"` / `call_outcome="used"`.

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest
from moralstack.utils.output_protection import OutputProtector

_DRAFT_TEXT = "CLIENT-MODEL-C VERBATIM BENIGN DRAFT"


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


def _benign_decision() -> Decision:
    return Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=[],
    )


def _build_controller(emitter: _RecordingEmitter) -> OrchestrationController:
    risk_result = RiskEstimation(
        score=0.05,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
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


def _run_benign(ctrl: OrchestrationController, req: ProcessedRequest):
    explanation = DecisionExplanation(
        request_id=req.request_id or "",
        final_action="NORMAL_COMPLETE",
        risk_score=0.05,
        risk_category="clearly_benign",
    )
    with (
        patch("moralstack.orchestration.controller.decide_action", return_value=(_benign_decision(), explanation)),
        patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        patch(
            "moralstack.orchestration.controller.get_route",
            return_value=("benign", False, RiskPolicyAction.ALLOW),
        ),
    ):
        return ctrl.process(req)


def _wait_for_speculative_row(emitter: _RecordingEmitter, timeout: float = 5.0) -> list[dict[str, Any]]:
    """The speculative row is persisted by `SpeculativeOverlapHandle.join_for_consumer`
    synchronously on the calling thread for a consumed route, but give it a
    generous deadline to avoid flakiness under load."""
    deadline = time.time() + timeout
    rows: list[dict[str, Any]] = []
    while time.time() < deadline:
        rows = [c for c in emitter.llm_calls if c.get("call_kind") == "speculative"]
        if rows:
            return rows
        time.sleep(0.02)
    return rows


class TestBenignUpstreamProvenance:
    def test_benign_delivers_draft_verbatim_no_separate_reuse_row(self) -> None:
        emitter = _RecordingEmitter()
        ctrl = _build_controller(emitter)

        req = ProcessedRequest(prompt="hello weather", request_id="req-benign-prov-1")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", _DRAFT_TEXT)

        result = _run_benign(ctrl, req)

        # Delivered text is the draft, verbatim.
        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == _DRAFT_TEXT

        # Final metadata carries provenance (from_decision, benign's own site).
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"

        speculative_rows = _wait_for_speculative_row(emitter)
        assert speculative_rows, "expected the speculative llm_call row to be persisted"
        row = speculative_rows[0]
        assert row.get("module") == "upstream_speculative"
        assert row.get("model") == "client-model-C"
        assert row.get("call_outcome") == "used"

        # Benign is NOT a reuse-row emitter: no separate llm_call with a
        # "speculative-reuse" action exists for this route.
        reuse_rows = [c for c in emitter.llm_calls if "speculative-reuse" in str(c.get("action") or "")]
        assert reuse_rows == [], f"benign route must not emit a reuse llm_call, got: {reuse_rows}"

        # Exactly one llm_call total for this route: the speculative row itself.
        assert len(emitter.llm_calls) == 1, f"expected exactly 1 llm_call (the speculative row), got: {emitter.llm_calls}"

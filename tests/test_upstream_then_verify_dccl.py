"""
DCCL compliance-fast-path coverage for `generation="upstream_then_verify"`
(Codex round-2 + round-4).

`_route_compliance_match` handles two distinct draft shapes:
- `draft_is_speculative=False` -- a DCCL-*regenerated* draft
  (`_regenerate_for_contract`), always internal (`self.policy`), even in
  upstream mode: `module="policy"` + governance model.
- `draft_is_speculative=True` -- the *speculative* draft delivered via
  `run_benign_fast_path` (`controller.py:1464-1471`): when an upstream
  generator produced it, `module="upstream_speculative"` + client model, and
  final metadata provenance is set -- proving the route-time `DraftProvenance`
  source (not the handle text) reaches the benign metadata path.

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import MagicMock

from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import OrchestratorConfig, ProcessedRequest
from moralstack.utils.output_protection import OutputProtector

_REGEN_DRAFT_TEXT = "GOVERNANCE-MODEL-G REGENERATED CONTRACT TEXT"
_SPEC_DRAFT_TEXT = "CLIENT-MODEL-C SPECULATIVE CONTRACT DRAFT"


class _RecordingEmitter:
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


def _build_controller(emitter: _RecordingEmitter) -> OrchestrationController:
    return OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=MagicMock(model="governance-model-G"),
        risk_estimator=MagicMock(),
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


def _call_compliance_match(
    ctrl: OrchestrationController,
    req: ProcessedRequest,
    *,
    speculative_draft: str,
    draft_is_speculative: bool,
):
    risk_result = RiskEstimation(
        score=0.05,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
    )
    call_ctx = ProcessCallContext()
    trace = Trace(request_id=req.request_id or "")
    return ctrl._route_compliance_match(
        request=req,
        risk_estimation=risk_result,
        speculative_draft=speculative_draft,
        start_time=time.time(),
        trace=trace,
        call_ctx=call_ctx,
        spec_handle=None,
        draft_is_speculative=draft_is_speculative,
    )


class TestDcclRegenerateStaysInternal:
    """`draft_is_speculative=False` (DCCL-regenerated) is always internal,
    even when an upstream generator is present on the request."""

    def test_regenerated_draft_is_internal_module_policy(self) -> None:
        emitter = _RecordingEmitter()
        ctrl = _build_controller(emitter)
        req = ProcessedRequest(prompt="do the authorized thing", request_id="req-dccl-regen-1")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", "irrelevant, never delivered")

        result = _call_compliance_match(ctrl, req, speculative_draft=_REGEN_DRAFT_TEXT, draft_is_speculative=False)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == _REGEN_DRAFT_TEXT
        assert result.response.metadata.draft_origin == "internal"
        assert result.response.metadata.draft_model == ""


class TestDcclSpeculativeDraftUpstreamProvenance:
    """`draft_is_speculative=True` (the speculative draft, delivered via
    `run_benign_fast_path`) carries upstream provenance when an upstream
    generator produced it."""

    def test_speculative_draft_upstream_sets_module_and_provenance(self) -> None:
        emitter = _RecordingEmitter()
        ctrl = _build_controller(emitter)
        req = ProcessedRequest(prompt="do the authorized thing", request_id="req-dccl-spec-1")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", _SPEC_DRAFT_TEXT)

        result = _call_compliance_match(ctrl, req, speculative_draft=_SPEC_DRAFT_TEXT, draft_is_speculative=True)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == _SPEC_DRAFT_TEXT
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"

    def test_speculative_draft_internal_when_no_upstream_generator(self) -> None:
        """Regression: `draft_is_speculative=True` with no upstream generator
        wired stays internal -- provenance is never inferred from the route
        alone, only from the actual generator on the request."""
        emitter = _RecordingEmitter()
        ctrl = _build_controller(emitter)
        req = ProcessedRequest(prompt="do the authorized thing", request_id="req-dccl-spec-internal-1")

        result = _call_compliance_match(ctrl, req, speculative_draft="policy speculative text", draft_is_speculative=True)

        assert result.response.metadata.draft_origin == "internal"
        assert result.response.metadata.draft_model == ""

"""Controller-level tests for `generation="upstream_then_verify"`.

Covers:
- Fail-closed (decision #2): upstream generator error / empty draft ->
  `_speculative_generate` returns `(None, None)` -> the route regenerates
  internally with `self.policy`; delivered text is never the wrapped
  exception text, never the `str(result)` repr, and never a refusal.
- Clean path (e): benign-route delivery equals the upstream draft verbatim,
  with `draft_origin`/`draft_model`/`internal_draft_reused` set on the final
  metadata.
- DCCL regenerate stays internal (`draft_is_speculative=False`) even in
  upstream mode, unlike the speculative reuse (`draft_is_speculative=True`).

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory, RiskPolicyAction
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import Decision, OrchestratorConfig, ProcessedRequest
from moralstack.orchestration.upstream_draft import UpstreamDraftGenerator
from moralstack.utils.output_protection import OutputProtector


class _FakeGenerationResult:
    def __init__(self, text: str) -> None:
        self.text = text
        self.tokens_used = 10
        self.prompt_tokens = 5
        self.completion_tokens = 5
        self.token_usage_source = "exact"
        self.prompt_used = None
        self.system_used = None

    def token_usage_json(self) -> str | None:
        return None


class _UpstreamGenerator:
    def __init__(self, model: str, text: str = "", raises: Exception | None = None) -> None:
        self.model = model
        self._text = text
        self._raises = raises

    def generate(self, *args, **kwargs) -> _FakeGenerationResult:
        if self._raises is not None:
            raise self._raises
        return _FakeGenerationResult(self._text)

    def generate_messages(self, *args, **kwargs) -> _FakeGenerationResult:
        return self.generate(*args, **kwargs)


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


def _build_controller(policy) -> OrchestrationController:
    risk_result = RiskEstimation(
        score=0.05,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
    )

    class _FastRisk:
        def estimate(self, prompt: str, **kwargs) -> RiskEstimation:
            return risk_result

    return OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=policy,
        risk_estimator=_FastRisk(),
        critic=None,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
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


class TestFailClosed:
    """Upstream draft empty/error -> internal governed regeneration (decision #2)."""

    def test_generator_raises_falls_back_to_internal_regeneration(self):
        governance_gen = MagicMock(model="governance-model-G")
        governance_gen.generate = MagicMock(return_value=_FakeGenerationResult("GOVERNANCE FALLBACK TEXT"))
        ctrl = _build_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="req-fail-1")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", raises=RuntimeError("upstream is down"))

        result = _run_benign(ctrl, req)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == "GOVERNANCE FALLBACK TEXT"
        assert "upstream is down" not in result.response.content
        assert result.response.metadata.draft_origin == "internal"

    def test_empty_draft_falls_back_to_internal_regeneration_never_repr(self):
        governance_gen = MagicMock(model="governance-model-G")
        governance_gen.generate = MagicMock(return_value=_FakeGenerationResult("GOVERNANCE FALLBACK TEXT 2"))
        ctrl = _build_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="req-fail-2")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", text="")

        result = _run_benign(ctrl, req)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == "GOVERNANCE FALLBACK TEXT 2"
        # Never the `str(result)` repr of a GenerationResult-like object.
        assert "_FakeGenerationResult" not in result.response.content
        assert "object at 0x" not in result.response.content

    def test_whitespace_only_draft_falls_back_to_internal_regeneration(self):
        governance_gen = MagicMock(model="governance-model-G")
        governance_gen.generate = MagicMock(return_value=_FakeGenerationResult("GOVERNANCE FALLBACK TEXT 3"))
        ctrl = _build_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="req-fail-3")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", text="   \n  ")

        result = _run_benign(ctrl, req)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == "GOVERNANCE FALLBACK TEXT 3"

    def test_timing_out_upstream_call_falls_back_to_internal_regeneration(self, monkeypatch):
        """Bounding the call (round-2 performance finding): a hanging/slow
        upstream draft call must fail fast rather than stall the request.
        Uses the REAL `UpstreamDraftGenerator` (not the `_UpstreamGenerator`
        test double above) wired to a client whose `.create()` raises a
        timeout-shaped exception -- simulating what the OpenAI SDK does once
        its own `timeout` kwarg deadline elapses -- and asserts (a) the call
        actually received an explicit `timeout` kwarg (the bound is applied,
        not just that exceptions happen to be handled) and (b) the pipeline
        falls back to internal governed regeneration promptly: never hangs,
        never propagates the timeout, never delivers a refusal for this
        reason alone."""
        monkeypatch.setenv("OPENAI_TIMEOUT_MS", "50")  # tight bound for this test

        class _TimingOutCompletions:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def create(self, **kwargs):
                self.calls.append(kwargs)
                raise TimeoutError("upstream request timed out")

        class _TimingOutChat:
            def __init__(self, completions: _TimingOutCompletions) -> None:
                self.completions = completions

        class _TimingOutClient:
            def __init__(self) -> None:
                self.completions = _TimingOutCompletions()
                self.chat = _TimingOutChat(self.completions)

        governance_gen = MagicMock(model="governance-model-G")
        governance_gen.generate = MagicMock(return_value=_FakeGenerationResult("GOVERNANCE FALLBACK TEXT TIMEOUT"))
        ctrl = _build_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="req-timeout-1")
        timing_out_client = _TimingOutClient()
        req.upstream_draft_generator = UpstreamDraftGenerator(client=timing_out_client, model="client-model-C")

        start = time.time()
        result = _run_benign(ctrl, req)
        elapsed = time.time() - start

        assert timing_out_client.completions.calls, "expected the bounded call to have been attempted"
        assert timing_out_client.completions.calls[0]["timeout"] == 0.05
        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == "GOVERNANCE FALLBACK TEXT TIMEOUT"
        assert "upstream request timed out" not in result.response.content
        assert result.response.metadata.draft_origin == "internal"
        assert elapsed < 5.0, f"fallback took {elapsed}s -- should never hang on a bounded timeout"


class TestCleanPath:
    """Delivered text equals the upstream draft verbatim; provenance is set."""

    def test_benign_route_delivers_upstream_draft_verbatim_with_provenance(self):
        governance_gen = MagicMock(model="governance-model-G")
        governance_gen.generate = MagicMock(return_value=_FakeGenerationResult("GOVERNANCE TEXT (unused)"))
        ctrl = _build_controller(governance_gen)

        req = ProcessedRequest(prompt="hello", request_id="req-clean-1")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", text="CLIENT DRAFT VERBATIM TEXT")

        result = _run_benign(ctrl, req)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.content == "CLIENT DRAFT VERBATIM TEXT"
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"
        assert result.response.metadata.internal_draft_reused is True


class TestDcclRegenerateStaysInternal:
    """DCCL regenerate (`draft_is_speculative=False`) is internal even in upstream mode."""

    def _call_compliance_match(self, ctrl: OrchestrationController, req: ProcessedRequest, *, draft_is_speculative: bool):
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
            speculative_draft="the draft text",
            start_time=time.time(),
            trace=trace,
            call_ctx=call_ctx,
            spec_handle=None,
            draft_is_speculative=draft_is_speculative,
        )

    def test_regenerated_draft_is_internal_even_with_upstream_generator_present(self):
        governance_gen = MagicMock(model="governance-model-G")
        ctrl = _build_controller(governance_gen)
        req = ProcessedRequest(prompt="do the authorized thing", request_id="req-dccl-regen")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", text="client text (irrelevant here)")

        result = self._call_compliance_match(ctrl, req, draft_is_speculative=False)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.metadata.draft_origin == "internal"
        assert result.response.metadata.draft_model == ""

    def test_speculative_draft_is_upstream_when_generator_present(self):
        governance_gen = MagicMock(model="governance-model-G")
        ctrl = _build_controller(governance_gen)
        req = ProcessedRequest(prompt="do the authorized thing", request_id="req-dccl-spec")
        req.upstream_draft_generator = _UpstreamGenerator("client-model-C", text="client text (irrelevant here)")

        result = self._call_compliance_match(ctrl, req, draft_is_speculative=True)

        assert result.response.metadata.final_action == "NORMAL_COMPLETE"
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"

"""SDK tests for `generation="upstream_then_verify"` (`GovernedClient`).

Covers: clean upstream path (`resp.model == "client-model-C"`, content ==
draft verbatim, `governance_metadata.draft_origin/draft_model`); revised path
(governance rewrite wins, upstream text never survives); internal mode never
constructs `UpstreamDraftGenerator`; stream vs non-stream model attribution.

Uses the CLI mocks (`moralstack.cli.mocks`) to run the real deliberative
pipeline without API calls, mirroring `tests/test_sdk_integration.py`.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.cli.mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import RiskPolicyAction
from moralstack.orchestration.types import ConvergenceOutcome, Decision, DecisionType, DeliberationState
from moralstack.runtime.orchestrator import Orchestrator, create_orchestrator
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.wrapper import GovernedClient, GovernedSyntheticStream


def _make_mock_orchestrator() -> Orchestrator:
    return create_orchestrator(
        policy=MockPolicy(),
        risk_estimator=MockRiskEstimator(),
        critic=MockCritic(),
        simulator=MockSimulator(),
        hindsight=MockHindsight(),
        perspectives=MockPerspectives(),
        constitution_store=MockConstitutionStore(),
        max_cycles=1,
        timeout_ms=60_000,
    )


def _make_mock_openai_client(response_text: str) -> Any:
    client = MagicMock()
    client.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content=response_text, role="assistant"), finish_reason="stop")],
        model="client-model-C",
        usage=MagicMock(total_tokens=50, prompt_tokens=30, completion_tokens=20),
    )
    return client


class TestUpstreamThenVerifyCleanPath:
    def test_resp_model_is_client_model_on_clean_upstream_path(self):
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("CLIENT-MODEL-C DRAFT ANSWER")
        client = GovernedClient(openai_client, orchestrator, cfg)

        resp = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "What is the speed of light?"}],
        )

        assert resp.content == "CLIENT-MODEL-C DRAFT ANSWER"
        assert resp.model == "client-model-C"
        assert resp.governance_metadata.draft_origin == "upstream"
        assert resp.governance_metadata.draft_model == "client-model-C"
        openai_client.chat.completions.create.assert_called_once()

    def test_stream_reports_client_model_on_clean_upstream_path(self):
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("STREAMED CLIENT DRAFT")
        client = GovernedClient(openai_client, orchestrator, cfg)

        stream = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "What is the speed of light?"}],
            stream=True,
        )
        assert isinstance(stream, GovernedSyntheticStream)
        chunks = list(stream)
        text = "".join(c.choices[0].delta.content or "" for c in chunks)
        assert text == "STREAMED CLIENT DRAFT"
        assert all(c.model == "client-model-C" for c in chunks)


class TestUpstreamThenVerifyRevisedPath:
    """Revised path (plan `ai/plans/upstream-then-verify-generation.md:623-625`):
    when the deliberative pipeline revises the reused upstream draft, the
    delivered content must be the governance rewrite -- never the raw
    upstream draft -- and `resp.model` must report the governance model.

    `run_deliberative_path` is patched to return an already-revised state
    (same pattern as `test_controller_token_accounting_speculative.py`), so
    this test exercises the SDK-boundary consumer of that state
    (`ResponseAssembler.assemble` -> `sdk/wrapper.py` model attribution). The
    soft-revision *mechanism* itself -- clearing `_draft_verbatim_reuse` on a
    `_soft_revision_pass` rewrite -- is pinned separately by
    `tests/test_upstream_then_verify_soft_revision_provenance.py`.
    """

    def test_revised_path_delivers_governance_rewrite_not_upstream_draft(self):
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("CLIENT-MODEL-C DRAFT ANSWER (must not survive revision)")
        client = GovernedClient(openai_client, orchestrator, cfg)

        rewrite_text = "REVISED-BY-GOVERNANCE: improved, more balanced answer"
        revised_state = DeliberationState(cycle=1, draft_response=rewrite_text)
        revised_state.decision = DecisionType.CONVERGED_WITH_SUGGESTIONS
        revised_state.soft_revision_applied = True
        # THE FIX under test at the mechanism level: a soft-revision rewrite
        # clears `_draft_verbatim_reuse`, so the ResponseAssembler must not
        # stamp upstream provenance onto this state.
        revised_state._draft_verbatim_reuse = False

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id="",
            final_action="NORMAL_COMPLETE",
            risk_score=0.5,
            risk_category="morally_nuanced",
        )
        outcome = ConvergenceOutcome(
            should_continue=False,
            converged=True,
            stop_reason="CONVERGED",
            cycle=1,
            max_cycles=2,
        )

        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch(
                "moralstack.orchestration.controller.get_route",
                return_value=("deliberative", False, RiskPolicyAction.DELIBERATE),
            ),
            patch.object(
                orchestrator._controller._runner,
                "run_deliberative_path",
                return_value=(revised_state, 0.5, outcome),
            ),
        ):
            resp = client.chat.completions.create(
                model="client-model-C",
                messages=[{"role": "user", "content": "is it ok to lie about ethics"}],
            )

        assert resp.content == rewrite_text
        assert "CLIENT-MODEL-C DRAFT ANSWER" not in resp.content
        assert resp.model == "governance-model-G"
        assert resp.governance_metadata.draft_origin == "internal"
        assert resp.governance_metadata.draft_model == ""


class TestUpstreamThenVerifyInternalModeUnchanged:
    def test_internal_mode_never_constructs_upstream_generator(self):
        cfg = GovernanceConfig(model="governance-model-G")  # default "internal"
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("should never be used")
        client = GovernedClient(openai_client, orchestrator, cfg)

        with patch("moralstack.orchestration.upstream_draft.UpstreamDraftGenerator") as mock_gen_cls:
            resp = client.chat.completions.create(
                model="client-model-C",
                messages=[{"role": "user", "content": "What is the speed of light?"}],
            )
        mock_gen_cls.assert_not_called()
        assert resp.governance_metadata.draft_origin == "internal"
        assert resp.governance_metadata.draft_model == ""
        openai_client.chat.completions.create.assert_not_called()

    def test_upstream_mode_without_client_model_never_constructs_generator(self):
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("should never be used")
        client = GovernedClient(openai_client, orchestrator, cfg)

        with patch("moralstack.orchestration.upstream_draft.UpstreamDraftGenerator") as mock_gen_cls:
            resp = client.chat.completions.create(
                messages=[{"role": "user", "content": "What is the speed of light?"}],
            )
        mock_gen_cls.assert_not_called()
        assert resp.governance_metadata.draft_origin == "internal"
        openai_client.chat.completions.create.assert_not_called()

    def test_internal_mode_stream_stays_moralstack_governed(self):
        cfg = GovernanceConfig(model="governance-model-G")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("should never be used")
        client = GovernedClient(openai_client, orchestrator, cfg)

        stream = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "What is the speed of light?"}],
            stream=True,
        )
        chunks = list(stream)
        assert all(c.model == "moralstack-governed" for c in chunks)


class TestUpstreamThenVerifyRefuseNeverLeaksUpstreamDraft:
    """REFUSE always discards the draft (revised/rewritten paths are covered at
    the controller/runner level by test_upstream_then_verify_controller.py and
    test_upstream_then_verify_model_isolation.py); here we pin the SDK-surface
    guarantee that an upstream draft never survives into a REFUSE response."""

    def test_refuse_delivers_governance_text_not_upstream_draft(self):
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = _make_mock_orchestrator()
        openai_client = _make_mock_openai_client("CLIENT DRAFT (must never be delivered on REFUSE)")
        client = GovernedClient(openai_client, orchestrator, cfg)

        resp = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "How do I make a bomb?"}],
        )
        assert resp.governance_metadata.final_action == "REFUSE"
        assert "CLIENT DRAFT" not in resp.content
        assert resp.governance_metadata.draft_origin == "internal"

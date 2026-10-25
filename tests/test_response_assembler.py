"""
Characterization tests for ResponseAssembler.

Documents current behavior of assemble and format_refusal.
"""

import pytest

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.types import (
    Decision,
    DeliberationState,
    ProcessedRequest,
    ResponseType,
)


def _make_decision(
    final_action: str = "NORMAL_COMPLETE",
    path: str = "DELIBERATIVE_PATH",
) -> Decision:
    """Minimal Decision for assembler tests."""
    return Decision(
        final_action=final_action,
        path=path,
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
    )


def _make_decision_explanation(reason_codes: list[str] | None = None) -> DecisionExplanation:
    """Minimal DecisionExplanation for assembler tests."""
    return DecisionExplanation(reason_codes=reason_codes or ["policy_bounds_decision"])


def test_format_refusal():
    """format_refusal produces template with reason and alternatives."""
    assembler = ResponseAssembler()
    result = assembler.format_refusal(
        reason="This request is harmful.",
        alternatives=["Try a safer approach instead."],
    )
    assert "This request is harmful." in result
    assert "- Try a safer approach instead." in result


def test_format_refusal_no_alternatives():
    """format_refusal with empty alternatives uses REFUSAL_ALTERNATIVES marker."""
    assembler = ResponseAssembler()
    result = assembler.format_refusal(reason="Cannot help.", alternatives=[])
    assert "Cannot help." in result
    assert "[REFUSAL_ALTERNATIVES]" in result


def test_assemble_refuse():
    """Decision REFUSE produces FinalResponse with response_type FULL_REFUSAL."""
    assembler = ResponseAssembler()
    state = DeliberationState(cycle=1, draft_response="")
    request = ProcessedRequest(prompt="Harmful request")
    decision = _make_decision(final_action="REFUSE")
    explanation = _make_decision_explanation()

    response = assembler.assemble(
        request=request,
        state=state,
        decision=decision,
        decision_explanation=explanation,
    )
    assert response.response_type == ResponseType.FULL_REFUSAL
    assert response.metadata.final_action == "REFUSE"
    assert response.metadata.must_refuse is True


def test_assemble_safe_complete():
    """Decision SAFE_COMPLETE produces FinalResponse with response_type WITH_CAVEAT."""
    assembler = ResponseAssembler()
    state = DeliberationState(cycle=1, draft_response="Here is the response with caveats.")
    request = ProcessedRequest(prompt="Question")
    decision = _make_decision(final_action="SAFE_COMPLETE")
    explanation = _make_decision_explanation()

    response = assembler.assemble(
        request=request,
        state=state,
        decision=decision,
        decision_explanation=explanation,
    )
    assert response.response_type == ResponseType.WITH_CAVEAT
    assert response.metadata.final_action == "SAFE_COMPLETE"
    assert response.content == "Here is the response with caveats."


def test_assemble_normal_complete():
    """Decision NORMAL_COMPLETE produces FinalResponse with response_type DIRECT."""
    assembler = ResponseAssembler()
    state = DeliberationState(cycle=1, draft_response="Direct answer.")
    request = ProcessedRequest(prompt="Question")
    decision = _make_decision(final_action="NORMAL_COMPLETE")
    explanation = _make_decision_explanation()

    response = assembler.assemble(
        request=request,
        state=state,
        decision=decision,
        decision_explanation=explanation,
    )
    assert response.response_type == ResponseType.DIRECT
    assert response.metadata.final_action == "NORMAL_COMPLETE"
    assert response.content == "Direct answer."


def test_draft_is_valid_refusal_returns_false_when_critic_proceed():
    """When critic decision is PROCEED, draft is constructive content, not a valid refusal."""
    assembler = ResponseAssembler()
    critique = type("Critique", (), {"violations": [], "decision": "PROCEED"})()
    state = DeliberationState(
        cycle=1,
        draft_response="Here are five constructive tips to help your colleague.",
        critiques=[critique],
    )
    assert assembler._draft_is_valid_refusal(state) is False


def test_draft_is_valid_refusal_returns_false_when_short_draft():
    """Short draft is never a valid refusal."""
    assembler = ResponseAssembler()
    critique = type("Critique", (), {"violations": [], "decision": "REVISE"})()
    state = DeliberationState(cycle=1, draft_response="No.", critiques=[critique])
    assert assembler._draft_is_valid_refusal(state) is False


def test_draft_is_valid_refusal_returns_false_when_no_critic():
    """No critic means we cannot treat draft as validated refusal."""
    assembler = ResponseAssembler()
    state = DeliberationState(
        cycle=1,
        draft_response="I cannot assist with that request for policy reasons.",
        critiques=[],
    )
    assert assembler._draft_is_valid_refusal(state) is False


def test_draft_is_valid_refusal_returns_false_when_violations():
    """Any violations mean draft is not an approved refusal."""
    assembler = ResponseAssembler()
    v = type("V", (), {"principle_id": "P1", "constraint_type": "soft"})()
    critique = type("Critique", (), {"violations": [v], "decision": "REVISE"})()
    state = DeliberationState(
        cycle=1,
        draft_response="I cannot assist with that request.",
        critiques=[critique],
    )
    assert assembler._draft_is_valid_refusal(state) is False


# -- S3a: a failed/timed-out final critique must not word the refusal with a stale cycle's guidance --------------


def _stale_state(outcome: str, **crit_kw):
    from types import SimpleNamespace

    crit = SimpleNamespace(violations=[], revision_guidance="PREV-GUIDANCE suggest: X", decision="")
    for k, v in crit_kw.items():
        setattr(crit, k, v)
    state = DeliberationState(critiques=[crit])
    state._critique_outcome = outcome
    return state


def _refuse(state, rationale="RISK-RATIONALE", policy=None):
    from types import SimpleNamespace

    meta = SimpleNamespace(risk_score=None)
    risk = (
        SimpleNamespace(rationale=rationale, risk_category=SimpleNamespace(value="clearly_harmful"))
        if rationale is not None
        else None
    )
    resp = ResponseAssembler(policy=policy)._make_refusal(
        ProcessedRequest(prompt="p"), state, _make_decision("REFUSE"), meta, risk_estimation=risk
    )
    return resp


@pytest.mark.parametrize("outcome", ["failed", "timeout"])
def test_s3a_incomplete_critique_ignores_stale_guidance(outcome):
    resp = _refuse(_stale_state(outcome))
    reason = resp.metadata.refusal_reason
    assert reason.startswith("RISK-RATIONALE")
    assert "PREV-GUIDANCE" not in reason and "PREV-GUIDANCE" not in resp.content
    assert "- X" not in resp.content
    assert "[REFUSAL_ALTERNATIVES]" in resp.content


@pytest.mark.parametrize("outcome", ["completed", "skipped", ""])
def test_s3a_other_outcomes_keep_guidance(outcome):
    resp = _refuse(_stale_state(outcome))
    assert resp.metadata.refusal_reason.startswith("PREV-GUIDANCE")
    assert "- X" in resp.content


def test_s3a_failed_without_rationale_falls_back_to_marker():
    resp = _refuse(_stale_state("failed"), rationale=None)
    assert resp.metadata.refusal_reason == "[REFUSAL_FALLBACK]"


def test_s3a_failed_with_critical_violations_keeps_harmful_sentence():
    state = _stale_state("failed", has_critical_violations=True)
    resp = _refuse(state, rationale=None)
    assert resp.metadata.refusal_reason.startswith("Request classified as clearly harmful based on policy evaluation.")


def test_s3a_policy_path_prompt_has_rationale_not_stale_guidance():
    from tests.prompts_golden import capture as golden
    from tests.prompts_golden.fakes import RecordingPolicy

    policy = RecordingPolicy(with_native_messages=False, default_response=golden._REFUSAL_TEXT)
    with golden._record_persistence():
        _refuse(_stale_state("failed"), policy=policy)
    (call,) = policy.snapshot()
    assert "RISK-RATIONALE" in call.prompt
    assert "PREV-GUIDANCE" not in call.prompt


def test_s3a_incomplete_outcomes_mirror_runner_constants():
    from moralstack.orchestration.deliberation_runner import CRITIQUE_OUTCOME_FAILED, CRITIQUE_OUTCOME_TIMEOUT
    from moralstack.orchestration.response_assembler import _INCOMPLETE_CRITIQUE_OUTCOMES

    assert _INCOMPLETE_CRITIQUE_OUTCOMES == {CRITIQUE_OUTCOME_FAILED, CRITIQUE_OUTCOME_TIMEOUT}


def test_s3a_policy_path_without_risk_rationale_does_not_leak_stale_guidance():
    # refusal_context.rationale wins over the `rationale` kwarg when the risk estimation has one, so the stale
    # guidance only reaches the prompt when the risk rationale is empty: pin that case.
    from tests.prompts_golden import capture as golden
    from tests.prompts_golden.fakes import RecordingPolicy

    policy = RecordingPolicy(with_native_messages=False, default_response=golden._REFUSAL_TEXT)
    with golden._record_persistence():
        _refuse(_stale_state("failed"), rationale="", policy=policy)
    (call,) = policy.snapshot()
    assert "PREV-GUIDANCE" not in call.prompt

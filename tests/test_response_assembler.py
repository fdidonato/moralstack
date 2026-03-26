"""
Characterization tests for ResponseAssembler.

Documents current behavior of assemble and format_refusal.
"""

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

"""
Tests for Step 10 / design v1.3 section 3.8: RefusalContext extension + 7-priority hierarchy.
"""

from __future__ import annotations

from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.refusal_context import (
    RefusalContext,
    build_refusal_context,
    classify_refusal_focus,
)


def _make_turn(role: str, content: str):
    class _T:
        pass

    t = _T()
    t.role = role
    t.content = content
    return t


def _make_risk_estimation(
    *,
    harm_type: str = "",
    request_type: str = "",
    operational_risk: str = "",
    requested_instructions: bool = False,
    intent_to_harm: bool = False,
    intent_operational: bool = False,
    semantic_signals: list[str] | None = None,
):
    class _R:
        pass

    r = _R()
    r.harm_type = harm_type
    r.request_type = request_type
    r.operational_risk = operational_risk
    r.requested_instructions = requested_instructions
    r.intent_to_harm = intent_to_harm
    r.intent_operational = intent_operational
    r.semantic_signals = semantic_signals or []
    r.activated_signals = []
    return r


def _make_decision(risk_signals: list[str] | None = None):
    class _D:
        pass

    d = _D()
    d.risk_signals = risk_signals or []
    return d


class TestRefusalContextNewFields:
    """The two new fields default to empty strings (backward compat)."""

    def test_default_developer_contract_summary(self):
        ctx = RefusalContext()
        assert ctx.developer_contract_summary == ""

    def test_default_conversation_history_snippet(self):
        ctx = RefusalContext()
        assert ctx.conversation_history_snippet == ""

    def test_fields_are_frozen(self):
        ctx = RefusalContext()
        try:
            ctx.developer_contract_summary = "X"  # type: ignore[misc]
            raise AssertionError("Expected FrozenInstanceError")
        except Exception as e:
            assert "frozen" in str(type(e).__name__).lower() or "frozeninstance" in str(e).lower()


class TestBuildRefusalContextExtended:
    """build_refusal_context populates the new fields from inputs."""

    def test_empty_when_no_contract_no_history(self):
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="general",
            refusal_redirection="",
        )
        assert ctx.developer_contract_summary == ""
        assert ctx.conversation_history_snippet == ""

    def test_populates_contract_summary(self):
        contract = DeveloperContract.from_text("You are a medical assistant.")
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="healthcare",
            refusal_redirection="Consult your doctor.",
            developer_contract=contract,
        )
        assert "medical assistant" in ctx.developer_contract_summary

    def test_populates_history_snippet(self):
        history = [_make_turn("user", "Hello"), _make_turn("assistant", "Hi")]
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="general",
            refusal_redirection="",
            conversation_history=history,
        )
        assert "[user]: Hello" in ctx.conversation_history_snippet
        assert "[assistant]: Hi" in ctx.conversation_history_snippet

    def test_history_only_last_3_turns(self):
        history = [_make_turn("user", f"turn{i}") for i in range(10)]
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="general",
            refusal_redirection="",
            conversation_history=history,
        )
        assert "turn7" in ctx.conversation_history_snippet
        assert "turn9" in ctx.conversation_history_snippet
        assert "turn0" not in ctx.conversation_history_snippet

    def test_contract_summary_truncated_at_200_chars(self):
        long_text = "x" * 250
        contract = DeveloperContract.from_text(long_text)
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="general",
            refusal_redirection="",
            developer_contract=contract,
        )
        assert len(ctx.developer_contract_summary) == 200
        assert ctx.developer_contract_summary == long_text[:200]

    def test_history_snippet_truncates_each_turn_content_at_200_chars(self):
        long_content = "y" * 250
        history = [_make_turn("user", long_content)]
        ctx = build_refusal_context(
            risk_estimation=_make_risk_estimation(),
            decision=_make_decision(),
            domain="general",
            refusal_redirection="",
            conversation_history=history,
        )
        assert "yyy" in ctx.conversation_history_snippet
        assert ctx.conversation_history_snippet.count("y") == 200


class TestClassifyRefusalFocus7Priorities:
    """The new 7-priority structure (P0-P6). Hard signals (P0) cannot be overridden."""

    def test_p0_self_harm_wins_over_developer_contract(self):
        """P0 invariant: self-harm signal must not be overridden by structured contract."""
        contract = DeveloperContract.from_text(
            "You are a medical assistant. Redirect non-medical questions to general help.",
            mode="structured",
        )
        focus, _ = classify_refusal_focus(
            semantic_signals=["Q8:self_harm_suicide"],
            harm_type="",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=contract,
        )
        assert focus == "self_harm_crisis"

    def test_p0_child_safety_wins_over_developer_contract(self):
        contract = DeveloperContract.from_text(
            "You are a research assistant. Redirect family questions to social workers.",
            mode="structured",
        )
        focus, _ = classify_refusal_focus(
            semantic_signals=["Q17:minor_exploitation"],
            harm_type="",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=contract,
        )
        assert focus != "developer_contract_redirection"
        assert "child" in focus.lower() or "minor" in focus.lower()

    def test_p1_developer_contract_redirection_when_structured(self):
        """P1: structured contract with redirection wording wins when no P0 signal."""
        contract = DeveloperContract.from_text(
            "You are a medical assistant. Redirect non-medical questions.",
            mode="structured",
        )
        focus, guidance = classify_refusal_focus(
            semantic_signals=[],
            harm_type="financial",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=contract,
        )
        assert focus == "developer_contract_redirection"
        assert "medical assistant" in guidance.lower() or "redirect" in guidance.lower()

    def test_p1_skipped_for_opaque_mode(self):
        """P1 must NOT trigger for mode='opaque' (the default)."""
        contract = DeveloperContract.from_text(
            "You are a medical assistant. Redirect non-medical questions.",
            mode="opaque",
        )
        focus, _ = classify_refusal_focus(
            semantic_signals=[],
            harm_type="financial",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=contract,
        )
        assert focus != "developer_contract_redirection"

    def test_p1_skipped_when_no_redirection_keyword(self):
        """P1 needs a redirection keyword (redirect/refer to/consult) in raw_text."""
        contract = DeveloperContract.from_text(
            "You are a medical assistant. Answer carefully.",
            mode="structured",
        )
        focus, _ = classify_refusal_focus(
            semantic_signals=[],
            harm_type="financial",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=contract,
        )
        assert focus != "developer_contract_redirection"

    def test_no_contract_no_p1(self):
        """Backward compat: when developer_contract is None, behavior is unchanged."""
        focus_with_none, _ = classify_refusal_focus(
            semantic_signals=[],
            harm_type="financial",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
            developer_contract=None,
        )
        focus_legacy, _ = classify_refusal_focus(
            semantic_signals=[],
            harm_type="financial",
            request_type="",
            operational_risk="",
            requested_instructions=False,
            intent_to_harm=False,
            intent_operational=False,
        )
        assert focus_with_none == focus_legacy

"""
Test suite for v0.4 extensions of ConversationGovernanceState:
- TurnContext, TurnDecisionSummary, RefreshDecision
- should_full_refresh(*, current_turn=...) — new signature with backward compatibility

Note: legacy signature tests (should_full_refresh() without args) remain in
tests/test_conversation_readiness.py.

Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.3.
"""

from __future__ import annotations

from moralstack.orchestration.conversation_state import (
    ConversationGovernanceState,
    RefreshDecision,
    TurnContext,
    TurnDecisionSummary,
)


class TestTurnContext:
    """TurnContext is frozen and has sensible defaults."""

    def test_default_construction(self):
        tc = TurnContext()
        assert tc.current_domain is None
        assert tc.current_developer_contract_hash is None
        assert tc.current_hard_signals_present is False
        assert tc.current_risk_posture == "NORMAL"

    def test_explicit_fields(self):
        tc = TurnContext(
            current_domain="healthcare",
            current_developer_contract_hash="abc123",
            current_hard_signals_present=True,
            current_risk_posture="ESCALATED",
        )
        assert tc.current_domain == "healthcare"
        assert tc.current_developer_contract_hash == "abc123"
        assert tc.current_hard_signals_present is True
        assert tc.current_risk_posture == "ESCALATED"


class TestRefreshDecision:
    """RefreshDecision exposes __bool__ coercion for forward compatibility."""

    def test_truthy_when_should_refresh(self):
        rd = RefreshDecision(should_refresh=True)
        assert bool(rd) is True
        # Allows `if state.should_full_refresh(...):` if return type changes in the future.
        assert rd

    def test_falsy_when_should_not_refresh(self):
        rd = RefreshDecision(should_refresh=False)
        assert bool(rd) is False
        assert not rd

    def test_reason_codes_default_empty(self):
        rd = RefreshDecision(should_refresh=True)
        assert rd.reason_codes == ()

    def test_with_reason_codes(self):
        rd = RefreshDecision(should_refresh=True, reason_codes=("domain_change", "contract_change"))
        assert rd.reason_codes == ("domain_change", "contract_change")


class TestTurnDecisionSummary:
    """TurnDecisionSummary is frozen and has sensible defaults."""

    def test_construction(self):
        s = TurnDecisionSummary(
            turn_index=3,
            final_action="SAFE_COMPLETE",
            risk_score=0.45,
        )
        assert s.turn_index == 3
        assert s.final_action == "SAFE_COMPLETE"
        assert s.risk_score == 0.45
        assert s.winning_rule == ""
        assert s.was_cached is False


class TestShouldFullRefreshBackwardCompat:
    """
    CRITICAL: the legacy signature `should_full_refresh()` without arguments
    MUST continue to return True. This protects tests/test_conversation_readiness.py:18.
    """

    def test_legacy_call_returns_true(self):
        s = ConversationGovernanceState()
        assert s.should_full_refresh() is True

    def test_legacy_call_with_hint_true(self):
        s = ConversationGovernanceState(full_refresh_required_hint=True)
        assert s.should_full_refresh() is True

    def test_legacy_call_with_hint_false_still_true(self):
        """Legacy default is conservative: with hint=False and no current_turn, still returns True."""
        s = ConversationGovernanceState(full_refresh_required_hint=False)
        assert s.should_full_refresh() is True


class TestShouldFullRefreshNewSignature:
    """Tests for the new should_full_refresh(*, current_turn=...) signature."""

    def test_no_hard_signals_no_changes_returns_false(self):
        """Clean state + clean turn -> no refresh, cache/fast-path eligible."""
        s = ConversationGovernanceState(
            active_domain="general",
            last_developer_contract_hash="abc123",
            last_governance_posture="NORMAL",
        )
        tc = TurnContext(
            current_domain="general",
            current_developer_contract_hash="abc123",
            current_hard_signals_present=False,
            current_risk_posture="NORMAL",
        )
        assert s.should_full_refresh(current_turn=tc) is False

    def test_hard_signal_forces_refresh(self):
        s = ConversationGovernanceState(active_domain="general", last_governance_posture="NORMAL")
        tc = TurnContext(current_domain="general", current_hard_signals_present=True)
        assert s.should_full_refresh(current_turn=tc) is True

    def test_domain_change_forces_refresh(self):
        s = ConversationGovernanceState(active_domain="general")
        tc = TurnContext(current_domain="healthcare")
        assert s.should_full_refresh(current_turn=tc) is True

    def test_contract_change_forces_refresh(self):
        s = ConversationGovernanceState(
            active_domain="general",
            last_developer_contract_hash="abc",
        )
        tc = TurnContext(
            current_domain="general",
            current_developer_contract_hash="xyz",
        )
        assert s.should_full_refresh(current_turn=tc) is True

    def test_escalated_posture_forces_refresh(self):
        s = ConversationGovernanceState(
            active_domain="general",
            last_governance_posture="ESCALATED",
        )
        tc = TurnContext(current_domain="general", current_risk_posture="ESCALATED")
        assert s.should_full_refresh(current_turn=tc) is True

    def test_hard_constraints_history_forces_refresh(self):
        s = ConversationGovernanceState(
            active_domain="general",
            last_hard_constraints_triggered=("self_harm_crisis",),
        )
        tc = TurnContext(current_domain="general")
        assert s.should_full_refresh(current_turn=tc) is True

    def test_explicit_hint_forces_refresh_even_with_clean_turn(self):
        s = ConversationGovernanceState(
            active_domain="general",
            full_refresh_required_hint=True,
        )
        tc = TurnContext(current_domain="general", current_hard_signals_present=False)
        assert s.should_full_refresh(current_turn=tc) is True


class TestNewFields:
    """The three new additive fields on ConversationGovernanceState."""

    def test_defaults(self):
        s = ConversationGovernanceState()
        assert s.last_developer_contract_hash is None
        assert s.last_governance_posture == "NORMAL"
        assert s.turn_decisions_summary == ()

    def test_with_developer_contract_hash_helper(self):
        s = ConversationGovernanceState()
        s2 = s.with_developer_contract_hash("hash_abc")
        assert s2.last_developer_contract_hash == "hash_abc"
        # Immutability: original instance is unchanged.
        assert s.last_developer_contract_hash is None

    def test_with_developer_contract_hash_none(self):
        s = ConversationGovernanceState(last_developer_contract_hash="hash_abc")
        s2 = s.with_developer_contract_hash(None)
        assert s2.last_developer_contract_hash is None


class TestSummaryDictWithNewFields:
    """to_summary_dict() includes new v0.4 fields."""

    def test_new_fields_in_summary(self):
        s = ConversationGovernanceState(
            last_developer_contract_hash="hash_xyz",
            last_governance_posture="ELEVATED",
            turn_decisions_summary=(
                TurnDecisionSummary(
                    turn_index=0,
                    final_action="NORMAL_COMPLETE",
                    risk_score=0.1,
                ),
                TurnDecisionSummary(
                    turn_index=1,
                    final_action="SAFE_COMPLETE",
                    risk_score=0.45,
                    winning_rule="overlay_sensitive",
                    was_cached=True,
                ),
            ),
        )
        d = s.to_summary_dict()
        assert d["last_developer_contract_hash"] == "hash_xyz"
        assert d["last_governance_posture"] == "ELEVATED"
        assert len(d["turn_decisions_summary"]) == 2
        assert d["turn_decisions_summary"][0]["final_action"] == "NORMAL_COMPLETE"
        assert d["turn_decisions_summary"][1]["was_cached"] is True

    def test_backward_compat_dict_keys(self):
        """Legacy summary dict keys remain present with unchanged names."""
        s = ConversationGovernanceState(conversation_id="c1", turn_index=0)
        d = s.to_summary_dict()
        assert "conversation_id" in d
        assert "turn_index" in d
        assert "active_domain" in d
        assert "full_refresh_required_hint" in d
        assert d["conversation_id"] == "c1"

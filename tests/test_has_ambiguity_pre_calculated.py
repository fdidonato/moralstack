"""Test for Fix B/C: _has_ambiguity_or_dual_use must respect the pre-calculated flag."""

from moralstack.runtime.decision.safe_complete_policy import (
    PolicyContext,
    _has_ambiguity_or_dual_use,
)


def test_respects_explicit_false_with_medium_axes():
    """
    Pre-calculated False MUST be respected, even if misuse/actionability are MEDIUM.
    This is the q58 scenario: factual intent + non-operational, calibration guard capped
    HIGH→MEDIUM, but the upstream computation has correctly concluded no ambiguity.
    """
    ctx = PolicyContext(
        risk_category="sensitive",
        misuse_plausibility="MEDIUM",
        actionability_risk="MEDIUM",
        has_ambiguity_or_dual_use=False,
        intent_type="factual",
        intent_operational=False,
    )
    assert _has_ambiguity_or_dual_use(ctx) is False


def test_respects_explicit_true():
    """Pre-calculated True must propagate."""
    ctx = PolicyContext(
        risk_category="sensitive",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        has_ambiguity_or_dual_use=True,
    )
    assert _has_ambiguity_or_dual_use(ctx) is True


def test_high_high_with_operational_signal_remains_true():
    """
    Sanity check: if upstream correctly sets True for HIGH/HIGH with operational signal,
    the function returns True.
    """
    ctx = PolicyContext(
        risk_category="potentially_harmful",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        has_ambiguity_or_dual_use=True,
        intent_operational=True,
    )
    assert _has_ambiguity_or_dual_use(ctx) is True


def test_default_false_respected():
    """When PolicyContext uses default False, no fallback re-enables it."""
    ctx = PolicyContext(
        risk_category="sensitive",
        misuse_plausibility="MEDIUM",
        actionability_risk="LOW",
    )
    assert _has_ambiguity_or_dual_use(ctx) is False


def test_dcf_signals_to_policy_context_pre_calculates_flag():
    """DCF must pre-compute has_ambiguity_or_dual_use to stay coherent with runtime."""
    from moralstack.runtime.decision_correctness import (
        DecisionSignals,
        _signals_to_policy_context,
    )

    signals_q58 = DecisionSignals(
        risk_category="sensitive",
        misuse_plausibility="MEDIUM",
        actionability_risk="MEDIUM",
        intent_clarity="HIGH",
        intent_operational=False,
        intent_type="factual",
    )
    ctx_q58 = _signals_to_policy_context(signals_q58)
    assert ctx_q58.has_ambiguity_or_dual_use is False

    signals_op = DecisionSignals(
        risk_category="potentially_harmful",
        misuse_plausibility="MEDIUM",
        actionability_risk="MEDIUM",
        intent_clarity="HIGH",
        intent_operational=True,
        intent_type="advice",
    )
    ctx_op = _signals_to_policy_context(signals_op)
    assert ctx_op.has_ambiguity_or_dual_use is True

    signals_hh = DecisionSignals(
        risk_category="sensitive",
        misuse_plausibility="HIGH",
        actionability_risk="HIGH",
        intent_clarity="HIGH",
        intent_operational=False,
        intent_type="factual",
    )
    ctx_hh = _signals_to_policy_context(signals_hh)
    assert ctx_hh.has_ambiguity_or_dual_use is True

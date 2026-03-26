"""
Test per overlay sensitivity: schema, risk_score floor e CYCLES_EXHAUSTED fallback.
No LLM calls; synthetic contexts only.
"""

import sys
from pathlib import Path

from moralstack.constitution.schema import Overlay, OverlayYAML
from moralstack.models.risk import RiskCategory
from moralstack.orchestration.controller import OVERLAY_SENSITIVE_RISK_FLOOR
from moralstack.orchestration.types import (
    ConvergenceOutcome,
    Decision,
)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# =============================================================================
# Schema Tests
# =============================================================================


class TestOverlaySensitiveSchema:
    """Verifica che il campo `sensitive` sia correttamente gestito nello schema."""

    def test_overlay_yaml_default_false(self):
        """OverlayYAML senza `sensitive` ha default False."""
        oy = OverlayYAML(keywords=["test"])
        assert oy.sensitive is False

    def test_overlay_yaml_sensitive_true(self):
        """OverlayYAML con `sensitive: true` lo mantiene."""
        oy = OverlayYAML(keywords=["mental health"], sensitive=True)
        assert oy.sensitive is True

    def test_overlay_yaml_sensitive_false_explicit(self):
        """OverlayYAML con `sensitive: false` esplicito."""
        oy = OverlayYAML(keywords=["creative"], sensitive=False)
        assert oy.sensitive is False

    def test_overlay_default_false(self):
        """Overlay senza `sensitive` ha default False."""
        o = Overlay(domain="creative")
        assert o.sensitive is False

    def test_overlay_sensitive_true(self):
        """Overlay con `sensitive=True` lo mantiene."""
        o = Overlay(domain="mental_health", sensitive=True)
        assert o.sensitive is True

    def test_overlay_sensitive_propagation(self):
        """Overlay costruito da OverlayYAML propaga `sensitive`."""
        oy = OverlayYAML(
            keywords=["mental health"],
            sensitive=True,
            description="Mental health overlay",
        )
        overlay = Overlay(
            domain="mental_health",
            description=oy.description.strip(),
            keywords=[str(k) for k in oy.keywords if k],
            sensitive=oy.sensitive,
        )
        assert overlay.sensitive is True

    def test_overlay_sensitive_propagation_false(self):
        """Overlay costruito da OverlayYAML non-sensitive propaga False."""
        oy = OverlayYAML(
            keywords=["creative writing"],
            sensitive=False,
        )
        overlay = Overlay(
            domain="creative",
            keywords=[str(k) for k in oy.keywords if k],
            sensitive=oy.sensitive,
        )
        assert overlay.sensitive is False


# =============================================================================
# Controller Logic Tests (unit-level, isolated)
# =============================================================================

# Le funzioni seguenti testano la logica decisionale applicata nel controller
# in modo isolato, senza bisogno dell'intero orchestrator.


def _make_decision(
    final_action: str = "NORMAL_COMPLETE",
    path: str = "DELIBERATIVE_PATH",
    reason_codes: list[str] | None = None,
) -> Decision:
    """Helper per creare una Decision di test."""
    return Decision(
        final_action=final_action,
        path=path,
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=reason_codes or [],
    )


def _make_outcome(
    stop_reason: str = "CONVERGED",
    converged: bool = True,
    cycle: int = 2,
    max_cycles: int = 2,
) -> ConvergenceOutcome:
    """Helper per creare un ConvergenceOutcome di test."""
    return ConvergenceOutcome(
        should_continue=False,
        converged=converged,
        stop_reason=stop_reason,
        cycle=cycle,
        max_cycles=max_cycles,
    )


class TestOverlaySensitiveRiskFloor:
    """Verifica la logica risk_score floor per overlay sensitive."""

    def test_floor_applied_when_sensitive_and_low_score(self):
        """Overlay sensitive + risk_score 0.1 -> floor a 0.35."""
        risk_score = 0.1
        overlay_sensitive = True
        if overlay_sensitive and risk_score < OVERLAY_SENSITIVE_RISK_FLOOR:
            risk_score = OVERLAY_SENSITIVE_RISK_FLOOR
        assert risk_score == OVERLAY_SENSITIVE_RISK_FLOOR

    def test_floor_not_applied_when_not_sensitive(self):
        """Overlay non sensitive + risk_score 0.1 -> invariato."""
        risk_score = 0.1
        overlay_sensitive = False
        if overlay_sensitive and risk_score < OVERLAY_SENSITIVE_RISK_FLOOR:
            risk_score = OVERLAY_SENSITIVE_RISK_FLOOR
        assert risk_score == 0.1

    def test_floor_not_applied_when_score_above_threshold(self):
        """Overlay sensitive + risk_score 0.5 -> invariato (già sopra la soglia)."""
        risk_score = 0.5
        overlay_sensitive = True
        if overlay_sensitive and risk_score < OVERLAY_SENSITIVE_RISK_FLOOR:
            risk_score = OVERLAY_SENSITIVE_RISK_FLOOR
        assert risk_score == 0.5

    def test_floor_guarantees_deliberative_path_entry(self):
        """Il floor 0.35 è sopra la threshold_low 0.3, quindi forza il path deliberativo."""
        assert OVERLAY_SENSITIVE_RISK_FLOOR > 0.3, (
            "OVERLAY_SENSITIVE_RISK_FLOOR deve essere > threshold_low (0.3) "
            "per garantire l'ingresso nel path deliberativo"
        )


class TestCyclesExhaustedFallback:
    """Verifica la logica fallback SAFE_COMPLETE su CYCLES_EXHAUSTED."""

    def _apply_fallback(
        self,
        outcome: ConvergenceOutcome,
        decision: Decision,
        risk_category: RiskCategory,
        overlay_sensitive: bool,
    ) -> Decision:
        """Riproduce la logica di fallback del controller."""
        if (
            outcome.stop_reason == "CYCLES_EXHAUSTED"
            and decision.final_action == "NORMAL_COMPLETE"
            and (risk_category in (RiskCategory.SENSITIVE, RiskCategory.MORALLY_NUANCED) or overlay_sensitive)
        ):
            return Decision(
                final_action="SAFE_COMPLETE",
                path="DELIBERATIVE_PATH",
                intent_clarity=decision.intent_clarity,
                misuse_plausibility=decision.misuse_plausibility,
                actionability_risk=decision.actionability_risk,
                triggered_principles=decision.triggered_principles,
                hard_violations=decision.hard_violations,
                risk_signals=decision.risk_signals,
                reason_codes=list(decision.reason_codes) + ["cycles_exhausted_sensitive_fallback"],
            )
        return decision

    def test_cycles_exhausted_sensitive_fallback(self):
        """CYCLES_EXHAUSTED + overlay_sensitive -> SAFE_COMPLETE."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = _make_decision(final_action="NORMAL_COMPLETE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.BENIGN,
            overlay_sensitive=True,
        )
        assert result.final_action == "SAFE_COMPLETE"
        assert "cycles_exhausted_sensitive_fallback" in result.reason_codes

    def test_cycles_exhausted_risk_category_sensitive_fallback(self):
        """CYCLES_EXHAUSTED + risk_category SENSITIVE (senza overlay) -> SAFE_COMPLETE."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = _make_decision(final_action="NORMAL_COMPLETE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.SENSITIVE,
            overlay_sensitive=False,
        )
        assert result.final_action == "SAFE_COMPLETE"
        assert "cycles_exhausted_sensitive_fallback" in result.reason_codes

    def test_cycles_exhausted_risk_category_morally_nuanced_fallback(self):
        """CYCLES_EXHAUSTED + risk_category MORALLY_NUANCED -> SAFE_COMPLETE."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = _make_decision(final_action="NORMAL_COMPLETE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.MORALLY_NUANCED,
            overlay_sensitive=False,
        )
        assert result.final_action == "SAFE_COMPLETE"
        assert "cycles_exhausted_sensitive_fallback" in result.reason_codes

    def test_cycles_exhausted_non_sensitive_no_fallback(self):
        """CYCLES_EXHAUSTED + non-sensitive + BENIGN -> NORMAL_COMPLETE preservato."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = _make_decision(final_action="NORMAL_COMPLETE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.BENIGN,
            overlay_sensitive=False,
        )
        assert result.final_action == "NORMAL_COMPLETE"
        assert "cycles_exhausted_sensitive_fallback" not in result.reason_codes

    def test_cycles_exhausted_refuse_not_overridden(self):
        """CYCLES_EXHAUSTED + REFUSE -> REFUSE preservato (mai degradato)."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = _make_decision(final_action="REFUSE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.SENSITIVE,
            overlay_sensitive=True,
        )
        assert result.final_action == "REFUSE"

    def test_converged_no_fallback(self):
        """CONVERGED + sensitive -> nessun fallback (deliberazione riuscita)."""
        outcome = _make_outcome(stop_reason="CONVERGED", converged=True)
        decision = _make_decision(final_action="NORMAL_COMPLETE")
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.SENSITIVE,
            overlay_sensitive=True,
        )
        assert result.final_action == "NORMAL_COMPLETE"

    def test_fallback_preserves_decision_fields(self):
        """Il fallback preserva tutti i campi della decisione originale."""
        outcome = _make_outcome(stop_reason="CYCLES_EXHAUSTED", converged=False)
        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="MEDIUM",
            misuse_plausibility="HIGH",
            actionability_risk="MEDIUM",
            triggered_principles=["CORE.NM.1"],
            hard_violations=["HC.HIPAA.1"],
            risk_signals=["medical_advice"],
            reason_codes=["existing_code"],
        )
        result = self._apply_fallback(
            outcome,
            decision,
            risk_category=RiskCategory.SENSITIVE,
            overlay_sensitive=True,
        )
        assert result.final_action == "SAFE_COMPLETE"
        assert result.path == "DELIBERATIVE_PATH"
        assert result.intent_clarity == "MEDIUM"
        assert result.misuse_plausibility == "HIGH"
        assert result.actionability_risk == "MEDIUM"
        assert result.triggered_principles == ["CORE.NM.1"]
        assert result.hard_violations == ["HC.HIPAA.1"]
        assert result.risk_signals == ["medical_advice"]
        assert "existing_code" in result.reason_codes
        assert "cycles_exhausted_sensitive_fallback" in result.reason_codes

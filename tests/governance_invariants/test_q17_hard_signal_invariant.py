"""
Governance invariant: Q17 (minor_exploitation) MUST be a hard semantic signal.

This invariant guards against silent regressions where Q17 is removed from
_HARD_SEMANTIC_SIGNALS in path_router.py. Q17 is treated as Priority 1 in
refusal_context.classify_refusal_focus and is parsed by the risk estimator —
its absence from the hard-signal frozenset is a pre-existing inconsistency
fixed in Step 7 of the v0.4 multi-turn rollout.

If this test fails, do NOT silently re-allow Q17 to be soft: investigate
whether the change is intentional and update the design (v1.3 Q-signals
catalog) before patching the test.
"""

from __future__ import annotations

from moralstack.orchestration.path_router import _HARD_SEMANTIC_SIGNALS


class TestQ17HardSignalInvariant:
    def test_q17_is_in_hard_semantic_signals(self):
        assert "Q17:minor_exploitation" in _HARD_SEMANTIC_SIGNALS, (
            "Q17:minor_exploitation MUST be a hard semantic signal in path_router.py. "
            "It is Priority 1 in classify_refusal_focus (refusal_context.py); removing it "
            "from the hard-signal frozenset creates a silent inconsistency where queries "
            "with ONLY Q17 do not get a forced REFUSE route."
        )

    def test_all_priority1_signals_are_hard(self):
        """
        All signals classified as Priority 1 in classify_refusal_focus MUST be
        present in _HARD_SEMANTIC_SIGNALS. Currently: Q17, Q10, Q5.
        """
        priority1_signals = {
            "Q17:minor_exploitation",
            "Q10:weapons_explosives_toxins",
            "Q5:physical_harm",
        }
        missing = priority1_signals - _HARD_SEMANTIC_SIGNALS
        assert not missing, (
            f"The following Priority 1 signals are missing from _HARD_SEMANTIC_SIGNALS: {missing}. "
            f"This creates an inconsistency between classify_refusal_focus and path_router."
        )

    def test_hard_signals_has_exactly_eight_entries(self):
        """
        After Step 7 the frozenset has exactly 8 entries (Q4, Q5, Q8, Q9, Q10, Q11, Q12, Q17).
        This test is a defensive check: if the count changes, the change should be deliberate
        and documented in the design and the q_signals_catalog (Step 12).
        """
        assert len(_HARD_SEMANTIC_SIGNALS) == 8, (
            f"Expected 8 hard semantic signals after Step 7, got {len(_HARD_SEMANTIC_SIGNALS)}. "
            f"Current set: {sorted(_HARD_SEMANTIC_SIGNALS)}"
        )

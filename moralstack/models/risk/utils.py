"""
Utility functions per il risk estimator (pure, stateless).
"""

from __future__ import annotations


def _intent_type_from_request_type(
    request_type: str | None,
    *,
    intent_operational: bool = False,
    risk_score: float = 0.5,
) -> str:
    """Deriva intent_type da request_type per policy SAFE_COMPLETE
    (stesso mapping di decision_policy).

    Per sensitive_topic: non-operational + risk_score basso → factual (informativo/descrittivo).
    """
    if not request_type:
        return ""
    r = str(request_type).strip().lower()
    if r in ("crisis_support", "support_request"):
        return "support"
    if r == "factual_query":
        return "factual"
    if r == "creative_request":
        # Creative requests may involve sensitive themes (e.g. fiction about
        # poisons, weapons, violence).  Mapping to "factual" activated the
        # factual exemption in safe_complete_policy section (2), dropping
        # min_required from SAFE_COMPLETE to NORMAL_COMPLETE even when
        # risk_category=SENSITIVE and harm signals were active.
        # "creative" keeps the SENSITIVE path's default SAFE_COMPLETE.
        return "creative"
    if r == "ethical_dilemma":
        return "advice"
    if r == "sensitive_topic":
        # sensitive_topic should always request sensible framing
        return "advice"
    if r in ("potential_harm", "clear_harm"):
        return "advice"
    return ""

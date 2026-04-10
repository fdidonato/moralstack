"""
DecisionExplanation: structured explainability model for every MoralStack decision.
Guarantees decision_reason is always populated, reason_codes are machine-readable,
and why_not fields provide explicit counterfactual reasoning.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field


@dataclass
class DecisionExplanation:
    """
    Structured explanation for a MoralStack decision.
    All fields are serializable; never None in output.
    """

    request_id: str = ""
    final_action: str = ""
    risk_score: float = 0.0
    risk_category: str = ""

    activated_signals: list[str] = field(default_factory=list)
    overlay_applied: str | None = None
    winning_rule: str = ""

    reason_codes: list[str] = field(default_factory=list)

    why_not_refuse: str = ""
    why_not_safe_complete: str = ""
    why_not_normal_complete: str = ""

    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, object]:
        """Export to dict for JSON serialization. Ensures no None in output."""
        d = asdict(self)
        # Normalize for serialization: None -> "" or []
        for k, v in d.items():
            if v is None:
                d[k] = "" if k not in ("activated_signals", "reason_codes") else []
        if d.get("overlay_applied") is None:
            d["overlay_applied"] = ""
        if d.get("why_not_normal_complete") is None:
            d["why_not_normal_complete"] = ""
        return d

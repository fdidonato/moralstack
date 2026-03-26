"""
Decision layer: policy bounds and final_action from risk + domain + structured signals.
Single source of truth for SAFE_COMPLETE / NORMAL_COMPLETE / REFUSE.
"""

from moralstack.runtime.decision.safe_complete_policy import (
    Action,
    PolicyBounds,
    PolicyContext,
    compute_action_bounds,
    decide_final_action,
)

__all__ = [
    "Action",
    "PolicyBounds",
    "PolicyContext",
    "compute_action_bounds",
    "decide_final_action",
]

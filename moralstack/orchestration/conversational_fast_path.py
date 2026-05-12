"""
ConversationalFastPathRunner — applies cached governance decisions to skip deliberation.

When the SemanticDecisionLedger (Step 4) reports a hit for the current turn (consulted
by the controller in Step 6), this runner converts the cached metadata into a "patched"
Decision and a forced route, allowing the controller to bypass the full deliberation
pipeline (Critic / Simulator / Hindsight / Perspectives) and go directly to response
generation via the standard route helpers (_route_benign, _route_safe_complete, _route_refuse).

DAF-4 (design v1.3 §10): response content is NEVER cached. Only the governance metadata
(final_action, reason_codes, triggered_principles, ...) is reused; the response itself
is generated fresh for linguistic adequacy and turn-specific phrasing.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §3.5.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Literal, cast

from moralstack.orchestration.ledger import LedgerResult
from moralstack.orchestration.types import Decision, FinalActionStr

_LOG = logging.getLogger(__name__)


FastPathRoute = Literal["benign", "safe_complete", "refuse"]


_FINAL_ACTION_TO_ROUTE: dict[str, FastPathRoute] = {
    "NORMAL_COMPLETE": "benign",
    "SAFE_COMPLETE": "safe_complete",
    "REFUSE": "refuse",
}


class ConversationalFastPathRunner:
    """
    Stateless runner that converts a ledger cache hit into a (patched Decision, forced route).

    The runner does NOT hold ledger or storage references — it is purely a translation
    layer. The controller owns the lookup result and decides whether to apply it.

    Usage:
        runner = ConversationalFastPathRunner()
        if ledger_result is not None and ledger_result.is_hit:
            patched_decision, forced_route = runner.apply_cached_decision(
                ledger_result=ledger_result,
                current_decision=decision,
            )
            # Use patched_decision and forced_route instead of decision/route.
    """

    def apply_cached_decision(
        self,
        *,
        ledger_result: LedgerResult,
        current_decision: Decision,
    ) -> tuple[Decision, FastPathRoute]:
        """
        Build a patched Decision and the forced route from the cached metadata.

        The patched Decision preserves the current run's intent_clarity,
        misuse_plausibility, actionability_risk, and risk_signals (these are
        session-state, not decision-state, and reflect the fresh risk estimation).
        Action-level fields from the cache (final_action, triggered_principles,
        reason_codes) replace the current values; path, hard_violations, and risk
        signals stay on the current decision.

        Args:
            ledger_result: a LedgerResult with is_hit=True and a non-None cached_decision.
            current_decision: the Decision produced by decide_action() in the current run.

        Returns:
            (patched_decision, forced_route). The route is one of
            "benign" / "safe_complete" / "refuse".

        Raises:
            ValueError: when the ledger_result is not a hit, or cached_decision is None,
                or the cached final_action is not a recognized value.
        """
        if not ledger_result.is_hit:
            raise ValueError("apply_cached_decision requires a ledger_result with is_hit=True")
        cached = ledger_result.cached_decision
        if cached is None:
            raise ValueError("apply_cached_decision requires a non-None cached_decision on a hit")
        forced_route = _FINAL_ACTION_TO_ROUTE.get(cached.final_action)
        if forced_route is None:
            raise ValueError(
                f"Unknown final_action in CachedDecision: {cached.final_action!r}. "
                f"Expected one of: NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE."
            )

        patched_decision = replace(
            current_decision,
            final_action=cast(FinalActionStr, cached.final_action),
            triggered_principles=list(cached.triggered_principles),
            reason_codes=list(cached.reason_codes),
        )
        _LOG.debug(
            "ConversationalFastPathRunner applied cached decision: action=%s, route=%s, from_turn=%s, similarity=%.3f",
            cached.final_action,
            forced_route,
            ledger_result.from_turn,
            ledger_result.similarity,
        )
        return patched_decision, forced_route

    def is_safe_to_apply(
        self,
        *,
        ledger_result: LedgerResult,
        current_decision: Decision,
        current_route: str,
    ) -> bool:
        """
        Conservative safety gate: only apply the cached decision when it would not
        downgrade a stricter current run.

        Returns True when at least one of these conditions holds:
        - The cached final_action is REFUSE (always safe to refuse).
        - The current decision is non-deliberative (route is "benign", "safe_complete",
          "refuse", or "fast_path" — the deliberation has not been requested by the
          current run).

        Returns False when the current run is requesting deliberation (route is
        "deliberative" or "deliberative_loop") AND the cached decision is more
        permissive than REFUSE. In that case the safer choice is to let the current
        deliberation proceed.

        Step 8/9 may relax this gate after the deliberation refactor.

        Args:
            ledger_result: the LedgerResult (must be a hit).
            current_decision: the Decision from the current decide_action() call.
            current_route: the route from get_route() in the current run.

        Returns:
            True when the cached decision can be applied; False otherwise.
        """
        _ = current_decision  # Reserved for future gate refinements; API is stable for callers.
        if not ledger_result.is_hit or ledger_result.cached_decision is None:
            return False
        cached = ledger_result.cached_decision
        if cached.final_action == "REFUSE":
            return True
        if current_route in ("benign", "safe_complete", "refuse", "fast_path"):
            return True
        return False

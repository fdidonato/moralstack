"""
TokenCostTracker - OpenAI API cost tracking for MoralStack.

Computes the cost in euros of LLM calls based on prompt_tokens and completion_tokens.
Prices per 1M tokens (USD, from OpenAI pricing - update periodically).
"""

from __future__ import annotations

from dataclasses import dataclass

# USD prices per 1M tokens (input, output). Source: platform.openai.com/docs/pricing
# Update periodically; models not in table use gpt-4o fallback.
_OPENAI_PRICE_USD_PER_1M: dict[str, tuple[float, float]] = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o-2024-05-13": (2.50, 10.00),
    "gpt-4o-2024-08-06": (2.50, 10.00),
    "gpt-4o-mini-2024-07-18": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4-turbo": (10.00, 30.00),
    "gpt-3.5-turbo": (0.50, 1.50),
}

# EUR/USD rate for conversion (configurable via env)
_DEFAULT_EUR_PER_USD = 0.92


@dataclass
class CallCost:
    """Cost of a single API call."""

    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    cost_eur: float


class TokenCostTracker:
    """
    Tracks total cost of OpenAI API calls.

    Usage:
        tracker = TokenCostTracker()
        policy.set_cost_tracker(tracker)
        # ... execution ...
        print(tracker.get_summary_eur())
    """

    def __init__(self, eur_per_usd: float | None = None):
        self.eur_per_usd = eur_per_usd or _DEFAULT_EUR_PER_USD
        self._calls: list[CallCost] = []
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

    def add_call(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> None:
        """Records a call and computes its cost."""
        price = _OPENAI_PRICE_USD_PER_1M.get(model)
        if price is None:
            # Fallback: match by prefix (e.g. gpt-4o-2024-05-13 -> gpt-4o)
            for k in sorted(_OPENAI_PRICE_USD_PER_1M.keys(), key=len, reverse=True):
                if model.startswith(k):
                    price = _OPENAI_PRICE_USD_PER_1M[k]
                    break
        if price is None:
            price = (2.50, 10.00)  # gpt-4o as fallback

        input_usd = (prompt_tokens / 1_000_000) * price[0]
        output_usd = (completion_tokens / 1_000_000) * price[1]
        cost_usd = input_usd + output_usd
        cost_eur = cost_usd * self.eur_per_usd

        self._calls.append(
            CallCost(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost_usd,
                cost_eur=cost_eur,
            )
        )
        self._total_prompt_tokens += prompt_tokens
        self._total_completion_tokens += completion_tokens

    @property
    def total_cost_eur(self) -> float:
        """Total cost in euros."""
        return sum(c.cost_eur for c in self._calls)

    @property
    def total_cost_usd(self) -> float:
        """Total cost in USD."""
        return sum(c.cost_usd for c in self._calls)

    @property
    def total_tokens(self) -> int:
        """Total tokens (prompt + completion)."""
        return self._total_prompt_tokens + self._total_completion_tokens

    @property
    def call_count(self) -> int:
        """Number of recorded calls."""
        return len(self._calls)

    def get_summary_eur(self) -> str:
        """Cost summary in euros for console output."""
        if not self._calls:
            return "   Estimated cost: €0.00 (no calls tracked)"
        total_eur = self.total_cost_eur
        total_usd = self.total_cost_usd
        return (
            f"   Estimated cost: €{total_eur:.4f} (${total_usd:.4f}) | "
            f"{self.call_count} calls | "
            f"{self.total_tokens:,} tokens"
        )

    def reset(self) -> None:
        """Resets the tracker for a new run."""
        self._calls.clear()
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0

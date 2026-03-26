"""
Mock modules for MoralStack CLI testing without API.
"""

from typing import Any


class MockPolicy:
    """Policy mock for testing without API."""

    def generate(self, prompt: str, system: str = "", config: Any = None) -> Any:
        """Generates mock response."""
        from dataclasses import dataclass

        @dataclass
        class MockResult:
            text: str
            tokens_used: int = 50
            finish_reason: str = "stop"

        # Simulate basic responses
        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in ["bomb", "hack", "poison", "kill"]):
            return MockResult(
                text="I cannot assist with this request as it could cause harm. " "I can help you with something else."
            )

        return MockResult(text=f"[MOCK] Simulated response for: {prompt[:100]}...")


class MockRiskEstimator:
    """Risk estimator mock for testing."""

    def estimate(self, prompt: str) -> Any:
        """Estimates risk based on keywords."""
        from moralstack.models.risk import RiskCategory, RiskEstimation

        prompt_lower = prompt.lower()

        high_risk_keywords = ["bomb", "hack", "poison", "kill", "malware", "weapon"]
        if any(kw in prompt_lower for kw in high_risk_keywords):
            return RiskEstimation(
                score=0.9,
                confidence=0.95,
                risk_category=RiskCategory.CLEARLY_HARMFUL,
                semantic_signals=["harmful_keyword"],
            )

        medium_risk_keywords = ["depression", "suicide", "medication"]
        if any(kw in prompt_lower for kw in medium_risk_keywords):
            return RiskEstimation(
                score=0.5,
                confidence=0.8,
                risk_category=RiskCategory.SENSITIVE,
                semantic_signals=["sensitive_topic"],
            )

        return RiskEstimation(
            score=0.1,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
        )


class MockCritic:
    """Constitutional critic mock."""

    def critique(self, request: Any, response: str, constitution: Any = None, **kwargs) -> Any:
        """Mock critique."""
        from dataclasses import dataclass, field

        @dataclass
        class MockCritique:
            violations: list = field(default_factory=list)
            severity_score: float = 0.0
            has_critical_violations: bool = False
            revision_guidance: str = ""

        return MockCritique()


class MockSimulator:
    """Consequence simulator mock."""

    def simulate(self, request: Any, response: str, num_scenarios: int = 3, **kwargs) -> list:
        """Simulates mock consequences."""
        return []


class MockHindsight:
    """Hindsight evaluator mock."""

    def evaluate(self, request: str, response: str, consequences: list, **kwargs) -> Any:
        """Mock hindsight evaluation."""
        from dataclasses import dataclass, field

        @dataclass
        class MockHindsightScores:
            safety: float = 0.8
            helpfulness: float = 0.7
            honesty: float = 0.9
            total: float = 0.8

        @dataclass
        class MockAggregatedHindsight:
            expected_value: float = 0.8
            worst_case: float = 0.5
            variance: float = 0.1
            recommendation: str = "proceed"

        @dataclass
        class MockHindsightResult:
            evaluations: list = field(default_factory=list)
            aggregated: MockAggregatedHindsight = field(default_factory=MockAggregatedHindsight)

        return MockHindsightResult()


class MockPerspectives:
    """Perspective ensemble mock."""

    def evaluate(self, request: Any, response: str, **kwargs) -> Any:
        """Mock perspectives evaluation."""
        from dataclasses import dataclass, field

        @dataclass
        class MockPerspectiveAggregation:
            overall_score: float = 0.8
            concerns: list = field(default_factory=list)
            consensus_level: float = 0.9

        @dataclass
        class MockPerspectiveResult:
            results: list = field(default_factory=list)
            aggregation: MockPerspectiveAggregation = field(default_factory=MockPerspectiveAggregation)

        return MockPerspectiveResult()


class MockConstitutionStore:
    """Constitution store mock for testing without loading YAML or LLM."""

    def get_constitution(self, domain: str | None = None) -> Any:
        """Returns a minimal constitution with empty principles."""
        from dataclasses import dataclass, field

        @dataclass
        class MockConstitution:
            principles: list = field(default_factory=list)

        return MockConstitution()

    def get_relevant_principles(self, query: str, top_k: int = 10, domain: str | None = None) -> list:
        """Returns empty list (no principles needed for mock)."""
        return []

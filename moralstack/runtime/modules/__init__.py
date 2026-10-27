from typing import Any

"""
MoralStack Runtime Modules.

Cognitive modules for the deliberative process:
- critic_module: Constitutional critique
- simulator_module: Consequence simulation
- hindsight_module: Hindsight evaluation (safety/helpfulness/honesty)
- perspective_module: Ensemble of cognitive perspectives

Lazy imports: the concrete modules are loaded only on first access.
"""

__all__ = [
    # Critic module
    "CriticReport",
    "CriticConfig",
    "LLMConstitutionalCritic",
    "QuickCheckResult",
    "Violation",
    "create_critic",
    # Simulator module
    "Consequence",
    "ScenarioType",
    "SimulationResult",
    "SimulatorConfig",
    "LLMConsequenceSimulator",
    "create_simulator",
    # Hindsight module
    "HindsightConfig",
    "HindsightEvaluation",
    "HindsightRecommendation",
    "HindsightResult",
    "HindsightScores",
    "AggregatedHindsight",
    "LLMHindsightEvaluator",
    "create_hindsight_evaluator",
    # Perspective module
    "Perspective",
    "PerspectiveResult",
    "PerspectiveAggregation",
    "EnsembleConfig",
    "EnsembleResult",
    "LLMPerspectiveEnsemble",
    "DEFAULT_PERSPECTIVES",
    "PERSPECTIVES_BY_ID",
    "create_perspective_ensemble",
    "create_minimal_ensemble",
    "create_safety_focused_ensemble",
]

_LAZY_ATTRS: dict[str, str] = {
    "CriticReport": "moralstack.runtime.modules.critic_module",
    "CriticConfig": "moralstack.runtime.modules.critic_module",
    "LLMConstitutionalCritic": "moralstack.runtime.modules.critic_module",
    "QuickCheckResult": "moralstack.runtime.modules.critic_module",
    "Violation": "moralstack.runtime.modules.critic_module",
    "create_critic": "moralstack.runtime.modules.critic_module",
    "Consequence": "moralstack.runtime.modules.simulator_module",
    "ScenarioType": "moralstack.runtime.modules.simulator_module",
    "SimulationResult": "moralstack.runtime.modules.simulator_module",
    "SimulatorConfig": "moralstack.runtime.modules.simulator_module",
    "LLMConsequenceSimulator": "moralstack.runtime.modules.simulator_module",
    "create_simulator": "moralstack.runtime.modules.simulator_module",
    "HindsightConfig": "moralstack.runtime.modules.hindsight_module",
    "HindsightEvaluation": "moralstack.runtime.modules.hindsight_module",
    "HindsightRecommendation": "moralstack.runtime.modules.hindsight_module",
    "HindsightResult": "moralstack.runtime.modules.hindsight_module",
    "HindsightScores": "moralstack.runtime.modules.hindsight_module",
    "AggregatedHindsight": "moralstack.runtime.modules.hindsight_module",
    "LLMHindsightEvaluator": "moralstack.runtime.modules.hindsight_module",
    "create_hindsight_evaluator": "moralstack.runtime.modules.hindsight_module",
    "Perspective": "moralstack.runtime.modules.perspective_module",
    "PerspectiveResult": "moralstack.runtime.modules.perspective_module",
    "PerspectiveAggregation": "moralstack.runtime.modules.perspective_module",
    "EnsembleConfig": "moralstack.runtime.modules.perspective_module",
    "EnsembleResult": "moralstack.runtime.modules.perspective_module",
    "LLMPerspectiveEnsemble": "moralstack.runtime.modules.perspective_module",
    "DEFAULT_PERSPECTIVES": "moralstack.runtime.modules.perspective_module",
    "PERSPECTIVES_BY_ID": "moralstack.runtime.modules.perspective_module",
    "create_perspective_ensemble": "moralstack.runtime.modules.perspective_module",
    "create_minimal_ensemble": "moralstack.runtime.modules.perspective_module",
    "create_safety_focused_ensemble": "moralstack.runtime.modules.perspective_module",
}


def __getattr__(name: str) -> Any:
    """Lazy import to avoid import-time side effects."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

from typing import Any

"""
MoralStack Runtime Modules.

Moduli cognitivi per il processo deliberativo:
- critic_module: Critica costituzionale
- simulator_module: Simulazione conseguenze
- hindsight_module: Valutazione hindsight (safety/helpfulness/honesty)
- perspective_module: Ensemble di prospettive cognitive

Lazy imports: i moduli concreti vengono caricati solo al primo accesso.
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
    "SCENARIO_SEEDS",
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
    "SCENARIO_SEEDS": "moralstack.runtime.modules.simulator_module",
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
    """Import lazy per evitare side effects a import-time."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

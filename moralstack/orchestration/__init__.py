from typing import Any

"""
MoralStack Orchestration – coordinamento alto livello del flusso deliberativo.

Responsabilità separate in:
- controller: coordinamento e process()
- decision_service: decide_action (path/final_action)
- deliberation_runner: cicli e moduli (critic, simulator, perspectives, hindsight)
- diagnostics: logging, DCF (mai influenza flusso)
- response_assembler: costruzione risposta finale
- types: tipi condivisi
- trace: Trace request-scoped (diagnostica)

Lazy imports: i moduli concreti vengono caricati solo al primo accesso.
"""

__all__ = [
    "Decision",
    "DeliberationState",
    "FinalResponse",
    "OrchestrationController",
    "OrchestratorConfig",
    "OrchestratorResult",
    "ProcessedRequest",
    "ResponseMetadata",
    "RiskThresholds",
    "Trace",
]

_LAZY_ATTRS: dict[str, str] = {
    "Decision": "moralstack.orchestration.types",
    "DeliberationState": "moralstack.orchestration.types",
    "FinalResponse": "moralstack.orchestration.types",
    "OrchestratorConfig": "moralstack.orchestration.types",
    "OrchestratorResult": "moralstack.orchestration.types",
    "ProcessedRequest": "moralstack.orchestration.types",
    "ResponseMetadata": "moralstack.orchestration.types",
    "RiskThresholds": "moralstack.orchestration.types",
    "Trace": "moralstack.orchestration.trace",
    "OrchestrationController": "moralstack.orchestration.controller",
}


def __getattr__(name: str) -> Any:
    """Import lazy per evitare side effects a import-time."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

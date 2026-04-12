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
    "DefaultEventEmitter",
    "DeliberationState",
    "EventEmitter",
    "FinalResponse",
    "NullEventEmitter",
    "OrchestrationController",
    "OrchestratorConfig",
    "OrchestratorResult",
    "ProcessedRequest",
    "RefusalHandler",
    "ResponseMetadata",
    "RiskThresholds",
    "Trace",
    "evaluate_deliberation_override",
]

_LAZY_ATTRS: dict[str, str] = {
    "Decision": "moralstack.orchestration.types",
    "DefaultEventEmitter": "moralstack.orchestration.default_event_emitter",
    "DeliberationState": "moralstack.orchestration.types",
    "EventEmitter": "moralstack.orchestration.event_emitter",
    "FinalResponse": "moralstack.orchestration.types",
    "NullEventEmitter": "moralstack.orchestration.null_event_emitter",
    "OrchestratorConfig": "moralstack.orchestration.types",
    "OrchestratorResult": "moralstack.orchestration.types",
    "ProcessedRequest": "moralstack.orchestration.types",
    "RefusalHandler": "moralstack.orchestration.refusal_handler",
    "ResponseMetadata": "moralstack.orchestration.types",
    "RiskThresholds": "moralstack.orchestration.types",
    "Trace": "moralstack.orchestration.trace",
    "OrchestrationController": "moralstack.orchestration.controller",
    "evaluate_deliberation_override": "moralstack.orchestration.deliberation_override",
}


def __getattr__(name: str) -> Any:
    """Import lazy per evitare side effects a import-time."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""
MoralStack Runtime.

Runtime di inferenza che aggiunge ragionamento morale deliberativo a un LLM base.

Lazy imports: i moduli concreti vengono caricati solo al primo accesso.
"""

__all__ = [
    "CriticReport",
    "CriticConfig",
    "LLMConstitutionalCritic",
    "QuickCheckResult",
    "Violation",
    "create_critic",
]


def __getattr__(name: str):
    """Import lazy per evitare side effects a import-time."""
    if name in __all__:
        import importlib

        mod = importlib.import_module("moralstack.runtime.modules")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

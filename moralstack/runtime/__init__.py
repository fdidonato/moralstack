from typing import Any

"""
MoralStack Runtime.

Inference runtime that adds deliberative moral reasoning to a base LLM.

Lazy imports: concrete modules are loaded only on first access.
"""

__all__ = [
    "CriticReport",
    "CriticConfig",
    "LLMConstitutionalCritic",
    "QuickCheckResult",
    "Violation",
    "create_critic",
]


def __getattr__(name: str) -> Any:
    """Lazy import to avoid import-time side effects."""
    if name in __all__:
        import importlib

        mod = importlib.import_module("moralstack.runtime.modules")
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

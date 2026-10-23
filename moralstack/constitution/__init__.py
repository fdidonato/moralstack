from typing import Any

"""
MoralStack Constitution Module.

Manages ethical principles, the constitution and domain overlays.

Lazy imports: loader and store are loaded only on first access.
"""

__all__ = [
    "Principle",
    "Overlay",
    "Constitution",
    "ConstitutionStore",
    "ConstitutionStoreConfig",
    "ConstitutionLoadError",
    "ConstitutionValidationError",
    "OpenAIClientConfig",
    "REQUIRED_CORE_PRINCIPLE_ID",
    "create_store",
    "create_default_constitution",
]

_LAZY_ATTRS: dict[str, str] = {
    "ConstitutionLoadError": "moralstack.constitution.loader",
    "OpenAIClientConfig": "moralstack.constitution.openai_config",
    "Principle": "moralstack.constitution.store",
    "Overlay": "moralstack.constitution.store",
    "Constitution": "moralstack.constitution.store",
    "ConstitutionStore": "moralstack.constitution.store",
    "ConstitutionStoreConfig": "moralstack.constitution.store",
    "ConstitutionValidationError": "moralstack.constitution.store",
    "REQUIRED_CORE_PRINCIPLE_ID": "moralstack.constitution.store",
    "create_store": "moralstack.constitution.store",
    "create_default_constitution": "moralstack.constitution.store",
}


def __getattr__(name: str) -> Any:
    """Lazy import to avoid side effects at import time."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""
MoralStack Constitution Module.

Gestisce i principi etici, la costituzione e gli overlay di dominio.

Lazy imports: loader e store vengono caricati solo al primo accesso.
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


def __getattr__(name: str):
    """Import lazy per evitare side effects a import-time."""
    if name in _LAZY_ATTRS:
        import importlib

        mod = importlib.import_module(_LAZY_ATTRS[name])
        return getattr(mod, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

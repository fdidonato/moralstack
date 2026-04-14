"""
MoralStack - Runtime di inferenza con ragionamento morale deliberativo.

Un sistema che aggiunge ragionamento etico deliberativo a LLM base,
intercettando le richieste, valutando il rischio etico, e orchestrando
un processo di auto-critica e simulazione prima di produrre una risposta.

SDK pubblico::

    from moralstack import govern
    from openai import OpenAI

    client = govern(OpenAI())
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": "..."}],
    )
    print(response.content)
    print(response.governance_metadata.final_action)
"""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tomllib

try:
    __version__ = version("moralstack")
except PackageNotFoundError:
    # Fallback for local source execution before package metadata is available.
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        _pyproject = tomllib.load(f)
    __version__ = _pyproject["project"]["version"]

# SDK public API — lazy imports per evitare overhead a import time.
# Il costo della pipeline viene pagato solo alla prima chiamata govern().

__all__ = [
    "__version__",
    "govern",
    "GovernedClient",
    "GovernanceConfig",
    "GovernedResponse",
    "GovernanceMetadata",
    "GovernanceError",
    "GovernancePipelineError",
    "GovernanceConfigError",
    "GovernanceTimeoutError",
]

_SDK_NAMES = frozenset(__all__) - {"__version__"}


def __getattr__(name: str) -> object:
    if name in _SDK_NAMES:
        from moralstack import sdk as _sdk  # noqa: PLC0415

        obj = getattr(_sdk, name)
        # Metti nella cache del modulo per evitare lookup ripetuti
        import sys

        setattr(sys.modules[__name__], name, obj)
        return obj
    raise AttributeError(f"module 'moralstack' has no attribute {name!r}")

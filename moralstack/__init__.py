"""
MoralStack - Inference runtime with deliberative moral reasoning.

A system that adds deliberative ethical reasoning to base LLMs,
intercepting requests, assessing ethical risk, and orchestrating
a self-critique and simulation process before producing a response.

Public SDK::

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

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

try:
    __version__ = version("moralstack")
except PackageNotFoundError:
    # Fallback for local source execution before package metadata is available.
    pyproject_path = Path(__file__).resolve().parent.parent / "pyproject.toml"
    with pyproject_path.open("rb") as f:
        _pyproject = tomllib.load(f)
    __version__ = _pyproject["project"]["version"]

# SDK public API — lazy imports to avoid overhead at import time.
# The pipeline cost is paid only on the first govern() call.

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
        # Cache in the module to avoid repeated lookups
        import sys

        setattr(sys.modules[__name__], name, obj)
        return obj
    raise AttributeError(f"module 'moralstack' has no attribute {name!r}")

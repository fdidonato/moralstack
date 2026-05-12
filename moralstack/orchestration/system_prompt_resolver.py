"""
System prompt resolver — composes the effective system prompt per-request.

Replaces the legacy single global `protected_system_prompt` (constant per controller
instance) with a per-request composition that incorporates the DeveloperContract
(Step 1) when present. Byte-equality contract (design v1.3 §10): when
`request.developer_contract is None`, the returned string is byte-identical to the
legacy base for the same mode.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §8 Modifiche 1/3/4.
"""

from __future__ import annotations

from typing import Literal

from moralstack.orchestration._policy_helpers import (
    CONSTRAINED_GENERATION_INSTRUCTION,
    SAFE_COMPLETE_GENERATION_INSTRUCTION,
)
from moralstack.orchestration.types import ProcessedRequest

SystemPromptMode = Literal["normal", "safe_complete", "constrained"]


def effective_system_for_request(
    *,
    base: str,
    request: ProcessedRequest,
    mode: SystemPromptMode = "normal",
) -> str:
    """
    Compose the effective system prompt for the given request and pipeline mode.

    Composition rules:
    - When `request.developer_contract` is None: the composition starts from `base`
      alone (byte-identical to the legacy `self._protected_system_prompt` usage).
    - When `request.developer_contract.raw_text` is non-empty: the composition is
      `<contract.raw_text>\\n\\n<base>`. The developer contract is prefixed because
      it carries the deployer-declared identity; the base preserves the output
      protection guarantees added by the controller.

    Mode suffix:
    - "normal": no suffix.
    - "safe_complete": appends `"\\n\\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION`.
    - "constrained": appends `"\\n\\n" + CONSTRAINED_GENERATION_INSTRUCTION`.

    Args:
        base: the protected_system_prompt held by the controller/runner. May be empty.
        request: the ProcessedRequest. May have `developer_contract = None`.
        mode: pipeline mode determining the optional suffix.

    Returns:
        The effective system prompt string.

    Byte-equality invariant (when developer_contract is None):
        - mode="normal":         returns `base or ""`.
        - mode="safe_complete":  returns `(base or "") + "\\n\\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION`.
        - mode="constrained":    returns `(base or "") + "\\n\\n" + CONSTRAINED_GENERATION_INSTRUCTION`.
    """
    contract = getattr(request, "developer_contract", None)
    contract_text = ""
    if contract is not None:
        contract_text = getattr(contract, "raw_text", "") or ""

    base_text = base or ""

    if contract_text:
        composed = contract_text + "\n\n" + base_text
    else:
        composed = base_text

    if mode == "safe_complete":
        return composed + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION
    if mode == "constrained":
        return composed + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION
    return composed

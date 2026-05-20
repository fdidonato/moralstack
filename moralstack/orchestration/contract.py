"""
DeveloperContract — structured representation of the application-level contract
declared by the deployer (user system prompt).

The contract is a first-class citizen in the v0.4 pipeline. It is populated by
the SDK wrapper by extracting the `role='system'` message from `messages`, and
propagated through `ProcessedRequest.developer_contract`.

Modes:
- 'opaque' (default): keeps raw text without parsing. Modules consume it as
  deployer-declared rules that must be respected.
- 'structured' (opt-in): enriched with extracted scope/role/restrictions via a
  ContractExtractor LLM. Implemented in a future step, not in Step 1.

Normative reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.2.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Literal

from moralstack.compliance.types import StructuredRule

ContractMode = Literal["opaque", "structured"]


@dataclass(frozen=True)
class DeveloperContract:
    """
    Application contract declared by the deployer.

    Fields:
        raw_text: user system prompt, verbatim.
        mode: 'opaque' (default) or 'structured' (future).
        contract_hash: deterministic fingerprint sha256(raw_text + mode)[:16].
            Used as exact-match key in SemanticDecisionLedger (§5.2)
            and as part of context_fingerprint in module caches (§6.7).

    Fields used only in mode='structured' (None otherwise):
        declared_scope: declared scope (for example "medical assistant").
        declared_role: declared role (for example "diagnostic support").
        declared_restrictions: tuple of explicit restrictions.

    Immutability: frozen=True. Use `replace()` to build variants.
    """

    raw_text: str
    mode: ContractMode = "opaque"
    contract_hash: str = ""

    # Only for mode='structured':
    declared_scope: str | None = None
    declared_role: str | None = None
    declared_restrictions: tuple[str, ...] = field(default_factory=tuple)

    # DCCL integration (mode-agnostic):
    structured_rules: tuple[StructuredRule, ...] = field(default_factory=tuple)
    """
    Optional structured rules declared by the deployer for the DCCL.
    When empty (default), the DCCL falls back to LLM evaluation of raw_text.
    Reference: dccl_specification_v0.3.md section 4.1.
    """

    @classmethod
    def from_text(cls, text: str, mode: ContractMode = "opaque") -> DeveloperContract:
        """
        Canonical factory: builds a DeveloperContract from raw text,
        computing the deterministic contract_hash.

        Args:
            text: the user system prompt. Empty or whitespace-only strings
                still produce a valid contract (raw_text="" and stable hash);
                acceptance policy is delegated to the caller.
            mode: 'opaque' (default) or 'structured'.

        Returns:
            Immutable DeveloperContract with populated contract_hash.
        """
        normalized = text or ""
        digest_input = f"{normalized}|{mode}".encode("utf-8")
        contract_hash = hashlib.sha256(digest_input).hexdigest()[:16]
        return cls(
            raw_text=normalized,
            mode=mode,
            contract_hash=contract_hash,
        )

    def is_empty(self) -> bool:
        """True when the contract has no substantive content (empty or whitespace-only text)."""
        return not self.raw_text.strip()

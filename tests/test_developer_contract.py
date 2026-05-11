"""
Test suite for moralstack/orchestration/contract.py — DeveloperContract.

Reference: MORALSTACK_MULTITURN_DESIGN.md v1.3 §2.2.
"""

from __future__ import annotations

import dataclasses

import pytest

from moralstack.orchestration.contract import DeveloperContract


class TestFromText:
    """Tests for DeveloperContract.from_text() factory method."""

    def test_basic_construction(self):
        c = DeveloperContract.from_text("You are a helpful assistant")
        assert c.raw_text == "You are a helpful assistant"
        assert c.mode == "opaque"
        assert len(c.contract_hash) == 16

    def test_default_mode_is_opaque(self):
        c = DeveloperContract.from_text("system prompt")
        assert c.mode == "opaque"

    def test_explicit_structured_mode(self):
        c = DeveloperContract.from_text("system prompt", mode="structured")
        assert c.mode == "structured"

    def test_empty_string(self):
        c = DeveloperContract.from_text("")
        assert c.raw_text == ""
        assert len(c.contract_hash) == 16
        assert c.is_empty() is True

    def test_whitespace_only_string_is_empty(self):
        c = DeveloperContract.from_text("   \n\t  ")
        assert c.is_empty() is True

    def test_non_empty_string_is_not_empty(self):
        c = DeveloperContract.from_text("a")
        assert c.is_empty() is False


class TestContractHash:
    """Tests for contract_hash determinism and stability."""

    def test_hash_is_deterministic(self):
        c1 = DeveloperContract.from_text("identical text")
        c2 = DeveloperContract.from_text("identical text")
        assert c1.contract_hash == c2.contract_hash

    def test_hash_differs_for_different_text(self):
        c1 = DeveloperContract.from_text("text A")
        c2 = DeveloperContract.from_text("text B")
        assert c1.contract_hash != c2.contract_hash

    def test_hash_differs_for_different_mode(self):
        c1 = DeveloperContract.from_text("same text", mode="opaque")
        c2 = DeveloperContract.from_text("same text", mode="structured")
        assert c1.contract_hash != c2.contract_hash

    def test_hash_is_16_chars(self):
        c = DeveloperContract.from_text("x")
        assert len(c.contract_hash) == 16

    def test_hash_is_lowercase_hex(self):
        c = DeveloperContract.from_text("x")
        assert all(ch in "0123456789abcdef" for ch in c.contract_hash)

    def test_hash_stability_across_versions(self):
        """
        GUARD TEST: the contract_hash for a fixed input must never change
        across future MoralStack versions. If this test fails, the hashing
        algorithm changed and previous caches become invalid.
        """
        c = DeveloperContract.from_text("You are a careful assistant.", mode="opaque")
        # Precomputed expected value (sha256("You are a careful assistant.|opaque")[:16]):
        expected = "ec1b48ed5f8c25c7"
        assert c.contract_hash == expected, (
            f"contract_hash changed: got {c.contract_hash!r}, expected {expected!r}. "
            f"If intentional, update this test AND document the cache invalidation in CHANGELOG."
        )


class TestImmutability:
    """Tests that the contract is immutable (frozen=True)."""

    def test_cannot_assign_raw_text(self):
        c = DeveloperContract.from_text("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.raw_text = "y"  # type: ignore[misc]

    def test_cannot_assign_mode(self):
        c = DeveloperContract.from_text("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.mode = "structured"  # type: ignore[misc]

    def test_cannot_assign_contract_hash(self):
        c = DeveloperContract.from_text("x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            c.contract_hash = "abc"  # type: ignore[misc]


class TestStructuredFields:
    """Tests for structured-only fields (default None/empty tuple)."""

    def test_opaque_mode_structured_fields_are_default(self):
        c = DeveloperContract.from_text("x")
        assert c.declared_scope is None
        assert c.declared_role is None
        assert c.declared_restrictions == ()

    def test_can_construct_with_structured_fields_explicitly(self):
        """from_text does not populate structured fields; explicit construction is required."""
        c = DeveloperContract(
            raw_text="x",
            mode="structured",
            contract_hash="deadbeefdeadbeef",
            declared_scope="medical",
            declared_role="diagnostic_support",
            declared_restrictions=("no surgery", "no prescription"),
        )
        assert c.declared_scope == "medical"
        assert c.declared_role == "diagnostic_support"
        assert c.declared_restrictions == ("no surgery", "no prescription")

"""
Unit tests for moralstack/orchestration/system_prompt_resolver.py.
"""

from __future__ import annotations

from moralstack.orchestration._policy_helpers import (
    CONSTRAINED_GENERATION_INSTRUCTION,
    SAFE_COMPLETE_GENERATION_INSTRUCTION,
)
from moralstack.orchestration.contract import DeveloperContract
from moralstack.orchestration.system_prompt_resolver import effective_system_for_request
from moralstack.orchestration.types import ProcessedRequest


class TestNoDeveloperContract:
    """When developer_contract is None, the effective prompt depends only on base + mode."""

    def test_normal_mode_returns_base_verbatim(self):
        base = "You are a helpful assistant."
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        out = effective_system_for_request(base=base, request=request, mode="normal")
        assert out == base

    def test_safe_complete_mode_appends_suffix(self):
        base = "You are a helpful assistant."
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        out = effective_system_for_request(base=base, request=request, mode="safe_complete")
        assert out == base + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION

    def test_constrained_mode_appends_suffix(self):
        base = "You are a helpful assistant."
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        out = effective_system_for_request(base=base, request=request, mode="constrained")
        assert out == base + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION

    def test_empty_base_returns_empty_for_normal(self):
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        out = effective_system_for_request(base="", request=request, mode="normal")
        assert out == ""

    def test_empty_base_with_safe_complete_returns_only_suffix(self):
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        out = effective_system_for_request(base="", request=request, mode="safe_complete")
        assert out == "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION


class TestWithDeveloperContract:
    """When developer_contract is present, raw_text is prepended."""

    def test_normal_mode_prepends_contract(self):
        contract = DeveloperContract.from_text("You are a medical assistant.")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out = effective_system_for_request(base="Base text.", request=request, mode="normal")
        assert out == "You are a medical assistant.\n\nBase text."

    def test_safe_complete_mode_with_contract(self):
        contract = DeveloperContract.from_text("You are a medical assistant.")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out = effective_system_for_request(base="Base.", request=request, mode="safe_complete")
        assert out == "You are a medical assistant.\n\nBase." + "\n\n" + SAFE_COMPLETE_GENERATION_INSTRUCTION

    def test_constrained_mode_with_contract(self):
        contract = DeveloperContract.from_text("You are a medical assistant.")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out = effective_system_for_request(base="Base.", request=request, mode="constrained")
        assert out == "You are a medical assistant.\n\nBase." + "\n\n" + CONSTRAINED_GENERATION_INSTRUCTION

    def test_empty_contract_text_behaves_as_none(self):
        """A DeveloperContract with empty raw_text behaves as if no contract was provided."""
        contract = DeveloperContract.from_text("")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out = effective_system_for_request(base="Base.", request=request, mode="normal")
        assert out == "Base."

    def test_whitespace_contract_text_treated_as_present(self):
        """A DeveloperContract with whitespace-only raw_text IS treated as present (it has chars)."""
        # The resolver does not strip; the upstream extractor is responsible for that decision.
        # Here we document the behavior: whitespace-only raw_text is prepended verbatim.
        contract = DeveloperContract(raw_text="   ", mode="opaque", contract_hash="abc")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out = effective_system_for_request(base="Base.", request=request, mode="normal")
        assert out == "   \n\nBase."


class TestPurity:
    """The resolver does not mutate inputs and is fully deterministic."""

    def test_request_not_mutated(self):
        contract = DeveloperContract.from_text("Test.")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        before = request.developer_contract
        effective_system_for_request(base="Base.", request=request, mode="normal")
        assert request.developer_contract is before

    def test_deterministic(self):
        contract = DeveloperContract.from_text("Hi.")
        request = ProcessedRequest(prompt="hi", developer_contract=contract)
        out1 = effective_system_for_request(base="Base.", request=request, mode="normal")
        out2 = effective_system_for_request(base="Base.", request=request, mode="normal")
        assert out1 == out2


class TestModeValidation:
    """Unknown modes are silently treated as 'normal' (no suffix). This is documented behavior."""

    def test_unknown_mode_treated_as_normal(self):
        request = ProcessedRequest(prompt="hi", developer_contract=None)
        # Runtime forward-compatibility: values other than safe_complete/constrained append no suffix.
        out = effective_system_for_request(base="Base.", request=request, mode="unknown")  # type: ignore[arg-type]
        assert out == "Base."

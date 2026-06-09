"""
Tests for moralstack.orchestration.delivery — the pure governed delivery finalizer.

Plan 1 invariant: the delivered text is always the text already produced inside
the MoralStack governed pipeline. finalize_delivery never calls a model, never
touches the wrapped/upstream client, and never writes observability. The only
transformation it performs is the fail-closed substitution of a deterministic
safe refusal when the governed content is blank.
"""

from __future__ import annotations

from types import SimpleNamespace

from moralstack.orchestration.delivery import (
    GOVERNED_PIPELINE_REFUSAL_SOURCE,
    GOVERNED_REFUSAL_SOURCE,
    GOVERNED_TEXT_SOURCE,
    GovernedDelivery,
    finalize_delivery,
)
from moralstack.orchestration.final_revalidation import DEFAULT_POST_REVALIDATION_REFUSAL


def _make_result(final_action: str, content: str) -> SimpleNamespace:
    metadata = SimpleNamespace(final_action=final_action)
    response = SimpleNamespace(content=content, metadata=metadata)
    return SimpleNamespace(response=response)


class TestFinalizeDelivery:
    def test_normal_complete_delivers_governed_text_verbatim(self):
        result = _make_result("NORMAL_COMPLETE", "Here is the answer.")
        delivery = finalize_delivery(result, config=None)

        assert isinstance(delivery, GovernedDelivery)
        assert delivery.text == "Here is the answer."
        assert delivery.final_text_source == GOVERNED_TEXT_SOURCE
        assert delivery.final_action == "NORMAL_COMPLETE"
        assert delivery.finish_reason == "stop"
        assert delivery.original_final_action == "NORMAL_COMPLETE"
        assert delivery.empty_governed_content is False

    def test_safe_complete_delivers_governed_text_verbatim(self):
        result = _make_result("SAFE_COMPLETE", "Be careful with this.")
        delivery = finalize_delivery(result, config=None)

        assert delivery.text == "Be careful with this."
        assert delivery.final_text_source == GOVERNED_TEXT_SOURCE
        assert delivery.final_action == "SAFE_COMPLETE"
        assert delivery.finish_reason == "stop"

    def test_refuse_delivers_governed_refusal(self):
        result = _make_result("REFUSE", "I cannot help with that.")
        delivery = finalize_delivery(result, config=None)

        assert delivery.text == "I cannot help with that."
        assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE
        assert delivery.final_action == "REFUSE"
        assert delivery.finish_reason == "content_filter"
        assert delivery.empty_governed_content is False

    def test_blank_content_fails_closed(self):
        result = _make_result("NORMAL_COMPLETE", "   \n\t ")
        delivery = finalize_delivery(result, config=None)

        assert delivery.text == DEFAULT_POST_REVALIDATION_REFUSAL
        assert delivery.final_text_source == GOVERNED_PIPELINE_REFUSAL_SOURCE
        assert delivery.final_action == "REFUSE"
        assert delivery.finish_reason == "content_filter"
        # The pre-downgrade action is preserved for audit.
        assert delivery.original_final_action == "NORMAL_COMPLETE"
        assert delivery.empty_governed_content is True

    def test_empty_content_fails_closed(self):
        result = _make_result("SAFE_COMPLETE", "")
        delivery = finalize_delivery(result, config=None)

        assert delivery.text == DEFAULT_POST_REVALIDATION_REFUSAL
        assert delivery.final_text_source == GOVERNED_PIPELINE_REFUSAL_SOURCE
        assert delivery.final_action == "REFUSE"
        assert delivery.original_final_action == "SAFE_COMPLETE"
        assert delivery.empty_governed_content is True

    def test_config_argument_is_accepted_and_unused(self):
        # config is reserved for Plan 2; passing it must not change behavior.
        result = _make_result("NORMAL_COMPLETE", "ok")
        d1 = finalize_delivery(result, config=None)
        d2 = finalize_delivery(result, config=object())
        assert d1 == d2

    def test_governed_delivery_is_frozen(self):
        import dataclasses

        import pytest

        delivery = finalize_delivery(_make_result("NORMAL_COMPLETE", "x"), config=None)
        with pytest.raises(dataclasses.FrozenInstanceError):
            delivery.text = "mutated"  # type: ignore[misc]

"""
Tests for moralstack.orchestration.delivery — the pure governed delivery finalizer.

Plan 1 invariant: the delivered text is always the text already produced inside
the MoralStack governed pipeline. finalize_delivery never calls a model, never
touches the wrapped/upstream client, and never writes observability. It performs
two fail-closed substitutions of a deterministic safe refusal: blank governed
content, and a result whose decision label is outside the closed set
{NORMAL_COMPLETE, SAFE_COMPLETE, REFUSE}. A label that differs only in ASCII case or
surrounding whitespace is normalized.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from moralstack.orchestration.delivery import (
    GOVERNED_PIPELINE_REFUSAL_SOURCE,
    GOVERNED_REFUSAL_SOURCE,
    GOVERNED_TEXT_SOURCE,
    GovernedDelivery,
    finalize_delivery,
)
from moralstack.orchestration.final_revalidation import DEFAULT_POST_REVALIDATION_REFUSAL
from moralstack.orchestration.types import FinalAction


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


# ---------------------------------------------------------------------------
# F6 phase 5: an unlabelled result fails closed (defense in depth for 5.7)
# ---------------------------------------------------------------------------


def _assert_unlabelled_shape(delivery: GovernedDelivery, original: str) -> None:
    assert delivery.text == DEFAULT_POST_REVALIDATION_REFUSAL
    assert "REAL ANSWER" not in delivery.text
    assert delivery.final_text_source == GOVERNED_PIPELINE_REFUSAL_SOURCE
    assert delivery.final_action == "REFUSE"
    assert delivery.finish_reason == "content_filter"
    assert delivery.original_final_action == original
    assert delivery.empty_governed_content is False


class TestUnlabelledResultFailsClosed:
    @pytest.mark.parametrize(
        "label",
        [
            "",
            "ERROR",
            "BOGUS",
            "FinalAction.REFUSE",
            "garbage 123",
            "NORMAL COMPLETE",
            # Non-ASCII look-alikes: str.upper() would map U+017F onto "S".
            "ſafe_complete",
            "refuſe",
        ],
    )
    def test_unlabelled_result_is_refused_not_delivered(self, label: str) -> None:
        delivery = finalize_delivery(_make_result(label, "REAL ANSWER"))
        _assert_unlabelled_shape(delivery, label)

    @pytest.mark.parametrize("label", ["refuse", "Refuse", " REFUSE ", "refuse\n"])
    def test_label_case_and_whitespace_normalised_refuse(self, label: str) -> None:
        delivery = finalize_delivery(_make_result(label, "No."))
        assert delivery.final_action == "REFUSE"
        assert delivery.text == "No."
        assert delivery.final_text_source == GOVERNED_REFUSAL_SOURCE
        assert delivery.finish_reason == "content_filter"
        assert delivery.original_final_action == label

    @pytest.mark.parametrize(
        "label, canonical",
        [
            ("safe_complete", "SAFE_COMPLETE"),
            (" SAFE_COMPLETE\t", "SAFE_COMPLETE"),
            ("normal_complete", "NORMAL_COMPLETE"),
            ("Normal_Complete ", "NORMAL_COMPLETE"),
        ],
    )
    def test_label_case_and_whitespace_normalised_deliverable(self, label: str, canonical: str) -> None:
        delivery = finalize_delivery(_make_result(label, "Answer."))
        assert delivery.final_action == canonical
        assert delivery.text == "Answer."
        assert delivery.final_text_source == GOVERNED_TEXT_SOURCE
        assert delivery.finish_reason == "stop"
        assert delivery.original_final_action == label

    @pytest.mark.parametrize(
        "make, original",
        [
            (lambda: SimpleNamespace(response=SimpleNamespace(content="REAL ANSWER", metadata=None)), ""),
            (lambda: SimpleNamespace(response=SimpleNamespace(content="REAL ANSWER")), ""),
            (lambda: _make_result(FinalAction.REFUSE, "REAL ANSWER"), str(FinalAction.REFUSE)),
            (lambda: _make_result(0, "REAL ANSWER"), ""),
        ],
    )
    def test_missing_or_odd_metadata_fails_closed(self, make, original: str) -> None:
        _assert_unlabelled_shape(finalize_delivery(make()), original)

    def test_magicmock_metadata_fails_closed(self) -> None:
        from unittest.mock import MagicMock

        result = SimpleNamespace(response=SimpleNamespace(content="REAL ANSWER", metadata=MagicMock()))
        delivery = finalize_delivery(result)
        assert delivery.text == DEFAULT_POST_REVALIDATION_REFUSAL
        assert delivery.final_action == "REFUSE"
        assert delivery.finish_reason == "content_filter"
        assert delivery.empty_governed_content is False

    @pytest.mark.parametrize("result", [None, SimpleNamespace(response=None), _make_result("NORMAL_COMPLETE", " ")])
    def test_blank_control_legs_stay_on_blank_branch(self, result) -> None:
        delivery = finalize_delivery(result)
        assert delivery.text == DEFAULT_POST_REVALIDATION_REFUSAL
        assert delivery.final_text_source == GOVERNED_PIPELINE_REFUSAL_SOURCE
        assert delivery.empty_governed_content is True

    @pytest.mark.parametrize("content", ["x", "  lead", "tail  \n", "\u00e8\u00e0 \u2713", "[SYSTEM_ERROR]"])
    def test_valid_labels_and_blank_byte_identical(self, content: str) -> None:
        cases = {
            "NORMAL_COMPLETE": GovernedDelivery(content, "governed", "NORMAL_COMPLETE", "stop", "NORMAL_COMPLETE", False),
            "SAFE_COMPLETE": GovernedDelivery(content, "governed", "SAFE_COMPLETE", "stop", "SAFE_COMPLETE", False),
            "REFUSE": GovernedDelivery(content, "governed_refusal", "REFUSE", "content_filter", "REFUSE", False),
        }
        for label, expected in cases.items():
            result = _make_result(label, content)
            assert finalize_delivery(result) == expected
            assert finalize_delivery(result, config=object()) == expected
            assert result.response.content == content
            assert result.response.metadata.final_action == label
        for label in ("NORMAL_COMPLETE", "SAFE_COMPLETE", "REFUSE", "BOGUS", ""):
            blank = GovernedDelivery(
                DEFAULT_POST_REVALIDATION_REFUSAL, "governed_pipeline_refusal", "REFUSE", "content_filter", label, True
            )
            assert finalize_delivery(_make_result(label, "  \n")) == blank

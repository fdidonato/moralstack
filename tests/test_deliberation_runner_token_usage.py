"""Tests for deliberation_runner token usage JSON builder."""

from __future__ import annotations

import json

from moralstack.orchestration.deliberation_runner import _token_usage_json_from_result


def test_token_usage_json_builder_propagates_source():
    result = type(
        "Result",
        (),
        {
            "tokens_used": 120,
            "prompt_tokens": 70,
            "completion_tokens": 50,
            "token_usage_source": "exact",
        },
    )()
    payload = _token_usage_json_from_result(result)
    assert payload is not None
    data = json.loads(payload)
    assert data["source"] == "exact"
    assert data["total_tokens"] == 120


def test_token_usage_json_builder_defaults_source_when_attribute_absent():
    result = type(
        "Result",
        (),
        {"tokens_used": 120, "prompt_tokens": 70, "completion_tokens": 50},
    )()
    payload = _token_usage_json_from_result(result)
    assert payload is not None
    data = json.loads(payload)
    assert data["source"] == "unknown"
    assert '"prompt_tokens": 70' in payload
    assert '"completion_tokens": 50' in payload
    assert '"total_tokens": 120' in payload

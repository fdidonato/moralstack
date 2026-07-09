"""Cached prompt tokens must survive the module report objects.

The deliberative modules do not hand their ``GenerationResult`` to the
persistence path: they copy a few token fields into their own report objects
(``CriticReport``, ``SimulationResult``, ``HindsightResult``, ``EnsembleResult``),
and ``_token_usage_json_from_result`` reads *those*. A cached-token field that is
not copied there is silently dropped for exactly the modules the prompt-caching
work targets. These tests lock the copy sites.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from moralstack.models.base import GenerationResult
from moralstack.orchestration.deliberation_runner import _token_usage_json_from_result
from moralstack.runtime.modules.critic_module import CriticReport
from moralstack.runtime.modules.hindsight_module import HindsightResult
from moralstack.runtime.modules.perspective_module import EnsembleResult, PerspectiveResult
from moralstack.runtime.modules.simulator_module import SimulationResult


def _cached_from(result) -> int | None:
    payload = _token_usage_json_from_result(result)
    assert payload is not None
    return json.loads(payload).get("cached_input_tokens")


def test_generation_result_carries_cached_tokens_into_usage_json():
    result = GenerationResult(
        text="x",
        tokens_used=100,
        finish_reason="stop",
        prompt_tokens=70,
        completion_tokens=30,
        cached_prompt_tokens=64,
        token_usage_source="exact",
    )
    assert _cached_from(result) == 64
    assert json.loads(result.token_usage_json())["cached_input_tokens"] == 64


def test_report_objects_carry_cached_tokens_into_usage_json():
    common = dict(tokens_used=100, prompt_tokens=70, completion_tokens=30, token_usage_source="exact")
    for report in (
        CriticReport(cached_prompt_tokens=64, **common),
        SimulationResult(cached_prompt_tokens=64, **common),
        HindsightResult(cached_prompt_tokens=64, **common),
        EnsembleResult(cached_prompt_tokens=64, **common),
    ):
        assert _cached_from(report) == 64, type(report).__name__


def test_report_objects_without_cached_tokens_report_unknown_not_zero():
    common = dict(tokens_used=100, prompt_tokens=70, completion_tokens=30, token_usage_source="exact")
    for report in (CriticReport(**common), SimulationResult(**common), HindsightResult(**common)):
        assert _cached_from(report) is None, type(report).__name__


def test_ensemble_sums_cached_tokens_across_perspectives_and_ignores_unknown():
    from moralstack.runtime.modules.perspective_module import _sum_optional_token_field

    results = [
        PerspectiveResult("p1", tokens_used=10, prompt_tokens=8, completion_tokens=2, cached_prompt_tokens=4),
        PerspectiveResult("p2", tokens_used=10, prompt_tokens=8, completion_tokens=2, cached_prompt_tokens=None),
        PerspectiveResult("p3", tokens_used=10, prompt_tokens=8, completion_tokens=2, cached_prompt_tokens=6),
    ]
    assert _sum_optional_token_field(results, "cached_prompt_tokens") == 10

    all_unknown = [PerspectiveResult("p1", tokens_used=10, prompt_tokens=8, completion_tokens=2)]
    assert _sum_optional_token_field(all_unknown, "cached_prompt_tokens") is None


def test_legacy_result_without_the_field_stays_unknown():
    """Duck-typed doubles in the wild lack the attribute entirely."""
    legacy = SimpleNamespace(tokens_used=100, prompt_tokens=70, completion_tokens=30, token_usage_source="exact")
    assert _cached_from(legacy) is None

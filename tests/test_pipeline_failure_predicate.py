"""Shared pipeline-failure predicate (moralstack.reports.pipeline_failure).

The predicate reads STRUCTURED signals only: a synthetic ``SYSTEM.*`` failure
principle in ``meta_json.triggered_principles`` and the absence of a FINAL
decision-trace row. It never inspects the delivered text (PROJECT_SPEC 5.1).
"""

from __future__ import annotations

import pytest

from moralstack.reports.pipeline_failure import (
    PIPELINE_FAILURE_PRINCIPLES,
    pipeline_failure_principle,
)
from tests.test_benchmark_pipeline_failure_classification import _load_script


def test_principles_tuple_is_exact() -> None:
    assert PIPELINE_FAILURE_PRINCIPLES == ("SYSTEM.ERROR", "SYSTEM.TIMEOUT", "SYSTEM.FAIL_SAFE")


@pytest.mark.parametrize("principle", ["SYSTEM.ERROR", "SYSTEM.TIMEOUT", "SYSTEM.FAIL_SAFE"])
def test_each_principle_maps_to_itself(principle: str) -> None:
    assert pipeline_failure_principle([], {"triggered_principles": [principle]}) == principle


@pytest.mark.parametrize(
    "traces, meta",
    [
        ([], {"triggered_principles": ["SYSTEM.REQUIRES_DELIBERATION"]}),
        ([], {"triggered_principles": ["system.error"]}),
        ([{"stage": "FINAL"}], {"triggered_principles": ["SYSTEM.ERROR"]}),
        ([{"stage": " final "}], {"triggered_principles": ["SYSTEM.TIMEOUT"]}),
        ([], None),
        ([], []),
        ([], {"triggered_principles": "SYSTEM.ERROR"}),
        ([], {"triggered_principles": {"SYSTEM.ERROR": 1}}),
        ([], {"triggered_principles": None}),
        ([], {}),
        # Text is never a signal (PROJECT_SPEC 5.1).
        ([], {"final_response": "[SYSTEM_ERROR]"}),
    ],
)
def test_returns_none(traces, meta) -> None:
    assert pipeline_failure_principle(traces, meta) is None


def test_non_final_rows_do_not_suppress() -> None:
    traces = [{"stage": None}, {}, {"stage": "PRE_POLICY"}]
    assert pipeline_failure_principle(traces, {"triggered_principles": ["SYSTEM.FAIL_SAFE"]}) == "SYSTEM.FAIL_SAFE"


def test_non_string_entries_are_skipped() -> None:
    assert pipeline_failure_principle([], {"triggered_principles": [None, "SYSTEM.TIMEOUT"]}) == "SYSTEM.TIMEOUT"


def test_priority_follows_tuple_order() -> None:
    meta = {"triggered_principles": ["SYSTEM.TIMEOUT", "SYSTEM.ERROR"]}
    assert pipeline_failure_principle([], meta) == "SYSTEM.ERROR"


def test_ui_set_is_superset_of_benchmark_set(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_script(monkeypatch)
    bench = set(mod._PIPELINE_FAILURE_PRINCIPLES)
    ui = set(PIPELINE_FAILURE_PRINCIPLES)
    assert bench <= ui
    # D10a: the benchmark keeps FAIL_SAFE as a governance REFUSE; the UI is diagnostics.
    assert ui - bench == {"SYSTEM.FAIL_SAFE"}

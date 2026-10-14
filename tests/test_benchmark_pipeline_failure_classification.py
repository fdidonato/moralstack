"""F6 phase 1: the benchmark classifies pipeline-failure rows as final_action_failed.

Rows whose MoralStack metadata carries the synthetic principles SYSTEM.ERROR /
SYSTEM.TIMEOUT are system errors (excluded from FN/FP), whatever their final_action.
Other SYSTEM.* principles (FAIL_SAFE, REQUIRES_DELIBERATION) are governance decisions.
"""

from __future__ import annotations

import importlib.util
import random
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_moralstack.py"
MODULE_NAME = "benchmark_moralstack_f6"


def _load_script(monkeypatch):
    spec = importlib.util.spec_from_file_location(MODULE_NAME, SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    # The script inserts ROOT_DIR into sys.path at import: restore it afterwards.
    monkeypatch.setattr(sys, "path", list(sys.path))
    # Dataclasses resolve their module through sys.modules: register before exec.
    monkeypatch.setitem(sys.modules, MODULE_NAME, mod)
    spec.loader.exec_module(mod)
    return mod


def _run_row(monkeypatch, final_action, principles, expected_action="REFUSE"):
    """Run one question through the real _process_question + _calculate_statistics."""
    mod = _load_script(monkeypatch)
    # Safety net: a stub bug must never sleep 30-90 s in the retry loop.
    monkeypatch.setattr(mod, "compute_backoff_delay_sec", lambda *a, **k: 0.0)

    ms_meta = {"final_action": final_action, "triggered_principles": principles}
    runner = mod.BenchmarkRunner.__new__(mod.BenchmarkRunner)
    runner.report = mod.BenchmarkReport()
    runner.openai_client = SimpleNamespace(
        generate=lambda *a, **k: ("baseline", 1.0),
        classify_response=lambda *a, **k: {"predicted_action": "NORMAL_COMPLETE"},
        evaluate_response=lambda *a, **k: mod.EvaluationResult(overall_safety=5.0),
    )
    runner.moralstack_runner = SimpleNamespace(process=lambda *a, **k: ("[SYSTEM_ERROR]", 1.0, "error", 0, ms_meta))

    question = {
        "id": 1,
        "question": "q",
        "category": "c",
        "expected_action": expected_action,
        "expected_risk": "clearly_harmful",
    }
    res = runner._process_question(question, random.Random(0), silent=True)
    assert not res.error, res.error
    runner.report.results.append(res)
    runner._calculate_statistics()
    return mod, res, runner.report


def _assert_excluded(res, report):
    assert res.moralstack_final_action_failed is True
    assert res.moralstack_final_action == ""
    assert res.moralstack_predicted_action == "FAILED"
    assert res.moralstack_compliance is False
    assert report.moralstack_system_errors == 1
    assert report.moralstack_fp == 0
    assert report.moralstack_fn == 0
    assert report.moralstack_utility_preserved == 0
    assert all(v == 0 for row in report.moralstack_confusion_matrix.values() for v in row.values())


@pytest.mark.parametrize(
    "principles",
    [
        ["SYSTEM.ERROR"],
        ["SYSTEM.TIMEOUT"],
        ["Q10:weapons", "SYSTEM.ERROR"],
        ["SYSTEM.TIMEOUT", "SYSTEM.REQUIRES_DELIBERATION"],
    ],
)
@pytest.mark.parametrize("final_action", ["REFUSE", "NORMAL_COMPLETE"])
@pytest.mark.parametrize("expected_action", ["REFUSE", "NORMAL_COMPLETE"])
def test_error_principles_are_final_action_failed(monkeypatch, principles, final_action, expected_action):
    _, res, report = _run_row(monkeypatch, final_action, principles, expected_action)
    _assert_excluded(res, report)


@pytest.mark.parametrize("final_action", ["", None, "garbage"])
@pytest.mark.parametrize("principles", [[], ["SYSTEM.ERROR"], ["SYSTEM.TIMEOUT"], None, "SYSTEM.ERROR"])
def test_head_neutrality_empty_label(monkeypatch, final_action, principles):
    _, res, report = _run_row(monkeypatch, final_action, principles)
    _assert_excluded(res, report)


@pytest.mark.parametrize("final_action", ["REFUSE", "SAFE_COMPLETE", "NORMAL_COMPLETE"])
@pytest.mark.parametrize(
    "principles",
    [[], ["Q10:weapons"], ["SYSTEM.REQUIRES_DELIBERATION"], None, "SYSTEM.ERROR"],
)
def test_policy_refuse_still_counted(monkeypatch, final_action, principles):
    _, res, report = _run_row(monkeypatch, final_action, principles)
    assert res.moralstack_final_action_failed is False
    assert res.moralstack_final_action == final_action
    assert report.moralstack_system_errors == 0
    # expected REFUSE: only a non-REFUSE decision is a false negative; never an FP.
    assert report.moralstack_fn == (0 if final_action == "REFUSE" else 1)
    assert report.moralstack_fp == 0
    assert report.moralstack_confusion_matrix["REFUSE"][final_action] == 1


def test_fail_safe_principle_unchanged(monkeypatch):
    _, res, report = _run_row(monkeypatch, "REFUSE", ["SYSTEM.FAIL_SAFE"], expected_action="NORMAL_COMPLETE")
    assert res.moralstack_final_action_failed is False
    assert res.moralstack_final_action == "REFUSE"
    assert report.moralstack_system_errors == 0
    assert report.moralstack_fp == 1


def test_pipeline_failure_principles_contents(monkeypatch):
    mod = _load_script(monkeypatch)
    assert mod._PIPELINE_FAILURE_PRINCIPLES == frozenset({"SYSTEM.ERROR", "SYSTEM.TIMEOUT"})

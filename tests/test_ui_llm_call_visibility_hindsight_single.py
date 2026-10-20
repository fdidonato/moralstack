"""NEW-3 in the UI: the hindsight single-path retry row precedes the hindsight node (shared helpers:
``test_ui_llm_call_visibility.py``)."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator  # noqa: E402
from moralstack.runtime.modules.simulator_module import Consequence  # noqa: E402
from tests.test_llm_call_persistence_hindsight_single import _VALID_SINGLE, _Sequence  # noqa: E402
from tests.test_ui_llm_call_visibility import (  # noqa: E402
    T0,
    _call,
    _graph_sequence,
    _idx,
    _render,
    _scope,
    _seed_request,
    isolate_observability_fixture,  # noqa: F401  (autouse fixture, registered as "_isolate_observability")
    ui_client_fixture,  # noqa: F401  (a fixture, registered as "ui_client")
)

# --------------------------------------------------------------------------------------
# NEW-3: the hindsight single-path retry row precedes the hindsight evaluation
# --------------------------------------------------------------------------------------


def test_hindsight_single_failed_retry_precedes_the_hindsight_node(ui_client):
    run_id, request_id = "run-ui-hind", "req-ui-hind"
    _seed_request(run_id, request_id, path="DELIBERATIVE_PATH", total_cycles=1)
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="simulator",
        module="simulator",
        action="simulate",
        sequence_in_cycle=3,
        started_at=T0 + 100,
        duration_ms=500,
    )
    evaluator = LLMHindsightEvaluator(
        _Sequence(["bad", _VALID_SINGLE]), HindsightConfig(max_retries=2, enable_caching=False)
    )
    with _scope(run_id, request_id, cycle=1):
        evaluator.evaluate_scenario("req", "resp", Consequence(text="x", likelihood=0.5, scenario_id="s1"))
    get_obs().flush()
    _call(
        run_id,
        request_id,
        cycle=1,
        phase="hindsight",
        module="hindsight",
        action="evaluate",
        sequence_in_cycle=5,
        started_at=T0 + 700,
        duration_ms=500,
    )

    body = _render(ui_client, run_id, request_id)
    seq = _graph_sequence(body)

    simulate = _idx(seq, "simulate")
    retry = _idx(seq, "retry_failed_attempt_1")
    evaluate = _idx(seq, "evaluate")
    assert simulate < retry < evaluate, seq
    assert sum("Deliberation" in s for s in seq) == 1, seq
    # not a "parallel" sibling of the evaluation it retried; the pipe says why; failure styling; WHO failed
    assert not any("parallel" in s for s in seq[retry : evaluate + 1]), seq
    assert seq[evaluate - 1] == "retry after failed attempt", seq
    assert 'class="badge badge-fail" title="call_outcome">retry_failed' in body
    assert "failed: scenario s1" in body

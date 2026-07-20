"""
UI + markdown-export reader coverage for the `upstream_speculative` module
label (opt-in `generation="upstream_then_verify"`).

An `upstream_speculative` row is an upstream *speculative draft* (client
model) -- distinct from:
  - `"policy"` (the internal speculative draft), and
  - `"upstream_provider"` (a *final* provider candidate, swept into the
    "Final provider generation" post-output cycle).

Covers:
  * `_build_module_io_annotations` -- distinct "draft" shape, not the
    "candidate_final_text" shape used by `upstream_provider`.
  * `_TIMELINE_MODULE_ORDER` -- has a slot, adjacent to `policy`.
  * `_build_delivery_path_summary` -- step wording is "Upstream draft (client
    model)", never "Policy draft"; source label carries the distinct module.
  * `_normalize_post_output_cycles` -- `upstream_speculative` is NOT swept
    into "Final provider generation" (regression: only `upstream_provider`
    still is).
  * `main.css` -- a defined `[data-module="upstream_speculative"]` style
    (not falling back to an unrelated class).
  * Rendered request-detail page: a persisted `upstream_speculative` row
    renders with the distinct module attribute and step wording.
  * Markdown export (`_render_delivery_path_section`): same wording parity.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.emit_helpers import persist_llm_call, persist_orchestration_event  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request  # noqa: E402
from moralstack.orchestration.orchestration_event_taxonomy import SPECULATIVE_STARTED  # noqa: E402
from moralstack.reports.markdown_export import _render_delivery_path_section  # noqa: E402
from moralstack.ui.app import (  # noqa: E402
    _TIMELINE_MODULE_ORDER,
    _build_delivery_path_summary,
    _build_module_io_annotations,
    _normalize_post_output_cycles,
)
from tests.test_ui_conversation_views import (  # noqa: E402
    _bind_observability_db,
    _make_session_token,
    _reinstall_observability_service_writes,
    _reset_observability_singleton,
)

_MAIN_CSS_PATH = Path(__file__).resolve().parents[1] / "moralstack" / "ui" / "static" / "css" / "main.css"


@pytest.fixture(autouse=True)
def _isolate_observability() -> None:
    _reinstall_observability_service_writes()
    _reset_observability_singleton()


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_upstream_speculative.db")
    _bind_observability_db(monkeypatch, dbp)
    init_db(dbp)

    from moralstack.ui.app import create_app

    return TestClient(create_app(), follow_redirects=False)


# ---------------------------------------------------------------------------
# _build_module_io_annotations
# ---------------------------------------------------------------------------


class TestModuleIoAnnotations:
    def test_upstream_speculative_is_a_draft_not_a_final_candidate(self) -> None:
        call = {"module": "upstream_speculative", "raw_response": "CLIENT DRAFT TEXT", "cycle": 0}
        ann = _build_module_io_annotations(call)
        output_labels = {o["label"] for o in ann["outputs"]}
        assert "draft" in output_labels
        assert "candidate_final_text" not in output_labels

    def test_upstream_provider_is_still_a_final_candidate_no_regression(self) -> None:
        call = {"module": "upstream_provider", "raw_response": "FINAL PROVIDER TEXT", "cycle": 0}
        ann = _build_module_io_annotations(call)
        output_labels = {o["label"] for o in ann["outputs"]}
        assert "candidate_final_text" in output_labels
        assert "draft" not in output_labels

    def test_upstream_speculative_inputs_mirror_policy_speculative_shape(self) -> None:
        upstream_ann = _build_module_io_annotations({"module": "upstream_speculative", "cycle": 0})
        policy_ann = _build_module_io_annotations({"module": "policy", "cycle": 0})
        upstream_labels = {i["label"] for i in upstream_ann["inputs"]}
        policy_labels = {i["label"] for i in policy_ann["inputs"]}
        assert upstream_labels == policy_labels == {"risk", "principles"}


# ---------------------------------------------------------------------------
# _TIMELINE_MODULE_ORDER
# ---------------------------------------------------------------------------


class TestTimelineModuleOrder:
    def test_upstream_speculative_has_a_slot_adjacent_to_policy(self) -> None:
        assert "upstream_speculative" in _TIMELINE_MODULE_ORDER
        policy_idx = _TIMELINE_MODULE_ORDER.index("policy")
        upstream_spec_idx = _TIMELINE_MODULE_ORDER.index("upstream_speculative")
        assert upstream_spec_idx == policy_idx + 1


# ---------------------------------------------------------------------------
# _build_delivery_path_summary -- step wording
# ---------------------------------------------------------------------------


class TestDeliverySummaryUpstreamSpeculativeWording:
    def test_upstream_speculative_call_renders_upstream_draft_wording(self) -> None:
        llm_calls = [
            {
                "module": "upstream_speculative",
                "phase": "speculative_generate",
                "raw_response": "CLIENT DRAFT",
                "started_at": 1000,
            }
        ]
        summary = _build_delivery_path_summary(
            orchestration_events=[],
            traces=[],
            llm_calls=llm_calls,
            final_revalidation_info=None,
            proxy_output_info=None,
            final_response="CLIENT DRAFT",
        )
        spec_steps = [s for s in summary["steps"] if s["title"] == "Speculative draft generated"]
        assert spec_steps, "expected a 'Speculative draft generated' step"
        assert "Upstream draft (client model)" in spec_steps[0]["detail"]
        assert "Policy draft" not in spec_steps[0]["detail"]
        assert spec_steps[0]["source"] == "upstream_speculative/speculative_generate"

    def test_policy_speculative_call_keeps_policy_draft_wording_no_regression(self) -> None:
        llm_calls = [
            {
                "module": "policy",
                "phase": "speculative_generate",
                "raw_response": "INTERNAL DRAFT",
                "started_at": 1000,
            }
        ]
        summary = _build_delivery_path_summary(
            orchestration_events=[],
            traces=[],
            llm_calls=llm_calls,
            final_revalidation_info=None,
            proxy_output_info=None,
            final_response="INTERNAL DRAFT",
        )
        spec_steps = [s for s in summary["steps"] if s["title"] == "Speculative draft generated"]
        assert spec_steps
        assert "Policy draft" in spec_steps[0]["detail"]
        assert "Upstream draft (client model)" not in spec_steps[0]["detail"]
        assert spec_steps[0]["source"] == "policy/speculative_generate"


# ---------------------------------------------------------------------------
# _normalize_post_output_cycles -- upstream_speculative NOT swept
# ---------------------------------------------------------------------------


class TestNormalizePostOutputCyclesNoSweep:
    def test_upstream_speculative_is_not_swept_into_final_provider_generation(self) -> None:
        calls = [
            {"module": "risk_estimator", "cycle": 0},
            {"module": "upstream_speculative", "cycle": 0},
        ]
        _normalize_post_output_cycles(calls)
        spec_call = next(c for c in calls if c["module"] == "upstream_speculative")
        assert spec_call["cycle"] == 0
        assert spec_call.get("cycle_label") != "Final provider generation"

    def test_upstream_provider_is_still_swept_no_regression(self) -> None:
        calls = [
            {"module": "risk_estimator", "cycle": 0},
            {"module": "upstream_provider", "cycle": None},
        ]
        _normalize_post_output_cycles(calls)
        provider_call = next(c for c in calls if c["module"] == "upstream_provider")
        assert provider_call["cycle"] == 1
        assert provider_call["cycle_label"] == "Final provider generation"


# ---------------------------------------------------------------------------
# main.css -- defined style, not an unrelated fallback class
# ---------------------------------------------------------------------------


class TestMainCssStyle:
    def test_upstream_speculative_has_a_defined_flow_node_style(self) -> None:
        css_text = _MAIN_CSS_PATH.read_text(encoding="utf-8")
        assert '.flow-node[data-module="upstream_speculative"]' in css_text


# ---------------------------------------------------------------------------
# Rendered request-detail page
# ---------------------------------------------------------------------------


def test_request_page_renders_upstream_speculative_node_with_client_model(ui_client) -> None:
    run_id, request_id = "run-us-1", "req-us-1"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, request_id, prompt="hello", domain="general")
    persist_llm_call(
        run_id=run_id,
        request_id=request_id,
        phase="speculative_generate",
        module="upstream_speculative",
        action="generate (speculative)",
        model="client-model-C",
        raw_response="CLIENT DRAFT TEXT",
        call_kind="speculative",
        call_outcome="used",
    )
    persist_orchestration_event(
        run_id=run_id,
        request_id=request_id,
        stage="orchestration",
        component="speculative",
        event_type=SPECULATIVE_STARTED,
        decision="started",
        payload={"model": "client-model-C", "draft_origin": "upstream"},
    )
    get_obs().flush()

    token = _make_session_token(ui_client)
    resp = ui_client.get(f"/runs/{run_id}/requests/{request_id}", cookies={"moralstack_session": token})
    assert resp.status_code == 200, resp.text
    body = resp.text

    assert 'data-module="upstream_speculative"' in body
    assert "Upstream draft (client model)" in body


# ---------------------------------------------------------------------------
# Markdown export
# ---------------------------------------------------------------------------


class TestMarkdownExportDeliveryPathSection:
    def test_upstream_speculative_row_renders_upstream_draft_wording(self) -> None:
        calls = [
            {
                "module": "upstream_speculative",
                "phase": "speculative_generate",
                "started_at": 1000,
            }
        ]
        section = _render_delivery_path_section(events=[], traces=[], calls=calls)
        assert "Upstream draft (client model)" in section
        assert "Policy draft started" not in section

    def test_upstream_provider_row_still_renders_final_provider_candidate_no_regression(self) -> None:
        calls = [
            {
                "module": "upstream_provider",
                "phase": "upstream_regen",
                "started_at": 1000,
            }
        ]
        section = _render_delivery_path_section(events=[], traces=[], calls=calls)
        assert "Final provider candidate generated" in section

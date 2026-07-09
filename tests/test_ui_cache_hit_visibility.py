"""Prompt-cache hit rate must be visible wherever per-module / per-model tokens are.

Surfaces covered: the shared per-model panel (`_token_usage_view` →
`_token_usage.html`, rendered at four scopes), the per-module rollup and the
per-call badge on the request page, and the Domain retrieval table.

Invariant under test: a measured 0% (cache miss) and an unreported call ("—") are
never conflated.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from moralstack.ui.app import (  # noqa: E402
    _cache_hit_pct,
    _domain_retrieval_view,
    _module_summaries,
    _token_usage_view,
)


def test_cache_hit_pct_distinguishes_measured_zero_from_unknown():
    assert _cache_hit_pct(0, 100) == 0.0
    assert _cache_hit_pct(None, 100) is None
    assert _cache_hit_pct(64, 128) == 50.0
    assert _cache_hit_pct(10, 0) is None  # no input tokens: undefined, not 0%


def test_token_usage_view_reports_cache_hit_per_model_and_total():
    rows = [
        {
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "calls": 2,
            "cached_input_tokens": 640,
            "cached_usage_known": 2,
            "cached_input_base": 1000,
        },
        {
            "model": "gpt-4o-mini",
            "input_tokens": 1000,
            "output_tokens": 50,
            "total_tokens": 1050,
            "calls": 1,
            "cached_input_tokens": 0,
            "cached_usage_known": 1,
            "cached_input_base": 1000,
        },
    ]
    view = _token_usage_view(rows)
    assert view["by_model"][0]["cache_hit_pct"] == 64.0
    assert view["by_model"][1]["cache_hit_pct"] == 0.0  # measured miss, not unknown
    assert view["total_cached"] == 640
    assert view["cache_hit_pct"] == 32.0
    assert view["cached_known_calls"] == 3


def test_token_usage_view_hit_rate_is_not_diluted_by_unreported_calls():
    """A model mixing reported and unreported calls: the denominator is the reported input.

    Dividing 512 cached by the full 2000 input would print 25.6% and understate the
    real 51.2% hit rate on the calls the provider actually measured.
    """
    rows = [
        {
            "model": "gpt-4o",
            "input_tokens": 2000,
            "output_tokens": 100,
            "total_tokens": 2100,
            "calls": 2,
            "cached_input_tokens": 512,
            "cached_usage_known": 1,
            "cached_input_base": 1000,
        }
    ]
    view = _token_usage_view(rows)
    assert view["by_model"][0]["cache_hit_pct"] == 51.2
    assert view["cache_hit_pct"] == 51.2


def test_token_usage_view_cache_unknown_when_provider_reported_nothing():
    """Legacy DB rows: the column is absent/NULL, so no percentage may be shown."""
    rows = [
        {
            "model": "gpt-4o",
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "calls": 2,
            "cached_input_tokens": None,
            "cached_usage_known": 0,
            "cached_input_base": 0,
        }
    ]
    view = _token_usage_view(rows)
    assert view["by_model"][0]["cache_hit_pct"] is None
    assert view["cache_hit_pct"] is None


def test_module_summaries_report_cache_hit_and_unknown():
    calls = [
        {"module": "critic", "duration_ms": 10, "total_tokens": 120, "input_tokens": 100, "cached_input_tokens": 64},
        {"module": "critic", "duration_ms": 5, "total_tokens": 60, "input_tokens": 50, "cached_input_tokens": 0},
        # Different module, provider reported nothing (pre-migration row).
        {"module": "policy", "duration_ms": 5, "total_tokens": 60, "input_tokens": 50},
    ]
    summaries = _module_summaries(calls)
    assert summaries["critic"]["cached_tokens"] == 64
    assert summaries["critic"]["cache_hit_pct"] == pytest.approx(42.7, abs=0.1)
    assert summaries["policy"]["cache_hit_pct"] is None
    assert summaries["policy"]["cached_tokens"] is None


def test_module_summaries_hit_rate_is_not_diluted_by_unreported_calls():
    calls = [
        {"module": "policy", "duration_ms": 5, "total_tokens": 120, "input_tokens": 100, "cached_input_tokens": 50},
        {"module": "policy", "duration_ms": 5, "total_tokens": 120, "input_tokens": 100},  # unreported
    ]
    summaries = _module_summaries(calls)
    assert summaries["policy"]["cache_hit_pct"] == 50.0  # not 25.0
    assert summaries["policy"]["input_tokens"] == 200  # total input still reported as-is


def test_domain_retrieval_view_reports_cache_hit_per_call_and_total():
    calls = [
        {
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "model": "gpt-4o-mini",
            "total_tokens": 120,
            "input_tokens": 100,
            "output_tokens": 20,
            "cached_input_tokens": 50,
            "parsed_summary_json": json.dumps({"domain": "", "retrieval_phase": "risk"}),
        },
        {
            "module": "constitution_retriever",
            "action": "enhanced_domain_agent",
            "model": "gpt-4o-mini",
            "total_tokens": 60,
            "input_tokens": 50,
            "output_tokens": 10,
            "parsed_summary_json": json.dumps({"domain": "medical", "retrieval_phase": "risk"}),
        },
    ]
    view = _domain_retrieval_view(calls)
    by_action = {r["action"]: r for r in view["rows"]}
    assert by_action["domain_prefilter"]["cache_hit_pct"] == 50.0
    assert by_action["enhanced_domain_agent"]["cache_hit_pct"] is None
    assert view["total_cached"] == 50
    # 50/100 over the reported call only — the unreported call's 50 input tokens
    # must not dilute the rate down to 33.3%.
    assert view["cache_hit_pct"] == 50.0


def test_domain_retrieval_view_cache_all_unknown_stays_none():
    calls = [
        {
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "model": "m",
            "total_tokens": 60,
            "input_tokens": 50,
            "output_tokens": 10,
        }
    ]
    view = _domain_retrieval_view(calls)
    assert view["cache_hit_pct"] is None
    assert view["total_cached"] is None


# --------------------------------------------------------------------------- #
# End-to-end render: the percentage must actually reach the HTML.
# --------------------------------------------------------------------------- #

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.events import EVENT_LLM_CALL, make_envelope  # noqa: E402
from moralstack.observability.router import route  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)


def _bind_db(monkeypatch, dbp: str) -> None:
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_db_path", lambda: dbp)
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_observability_mode", lambda: "db_only")
    monkeypatch.setattr("moralstack.observability.router.get_observability_mode", lambda: "db_only")
    monkeypatch.setattr("moralstack.observability.read_store.get_db_path", lambda: dbp)


def _emit_cached(run_id, request_id, *, module, action, model, prompt, completion, cached):
    usage = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
        "source": "exact",
    }
    if cached is not None:
        usage["cached_input_tokens"] = cached
    route(
        make_envelope(
            EVENT_LLM_CALL,
            run_id=run_id,
            request_id=request_id,
            payload={
                "phase": "constitution_retrieval" if module == "constitution_retriever" else "deliberation",
                "module": module,
                "action": action,
                "model": model,
                "prompt": "p",
                "raw_response": "{}",
                "token_usage_json": json.dumps(usage),
                "billable_provider_call": True,
                "parsed_summary_json": json.dumps({"domain": "cyber", "retrieval_phase": "risk"}),
            },
        )
    )


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_cache.db")
    _bind_db(monkeypatch, dbp)
    init_db(dbp)
    from moralstack.ui import app as app_module

    monkeypatch.setenv("MORALSTACK_UI_USERNAME", "admin")
    monkeypatch.setenv("MORALSTACK_UI_PASSWORD", "test")
    app_module._UI_USERNAME = "admin"
    app_module._UI_PASSWORD = "test"
    client = TestClient(app_module.create_app(), follow_redirects=False)
    resp = client.post("/login", data={"username": "admin", "password": "test"}, follow_redirects=False)
    assert resp.status_code in (200, 303), resp.text
    return client


def _seed(run_id="run-cache", req_id="req-cache"):
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, req_id, prompt="hi", domain="cyber")
    update_request_meta(run_id, req_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    # 1024 of 2000 input tokens cached => 51.2% for gpt-4o.
    _emit_cached(
        run_id, req_id, module="policy", action="generate", model="gpt-4o", prompt=2000, completion=100, cached=1024
    )
    _emit_cached(
        run_id,
        req_id,
        module="constitution_retriever",
        action="enhanced_domain_agent",
        model="gpt-4o-mini",
        prompt=500,
        completion=50,
        cached=0,
    )
    return run_id, req_id


def test_request_page_renders_cache_hit_percentage(ui_client):
    run_id, req_id = _seed()
    body = ui_client.get(f"/runs/{run_id}/requests/{req_id}").text
    assert "Cache hit" in body  # per-model panel + domain-retrieval column headers
    assert "51.2%" in body  # gpt-4o per-model row and/or policy module rollup
    assert "cached" in body


def test_run_and_dashboard_pages_render_cache_hit(ui_client):
    run_id, _ = _seed()
    for url in ("/runs", f"/runs/{run_id}"):
        body = ui_client.get(url).text
        assert "Cache hit" in body, url
        assert "% cached" in body, url


def test_pages_render_without_cache_data_on_legacy_rows(ui_client):
    """No cached column values: pages still render, showing '—' not '0%'."""
    run_id, req_id = "run-legacy", "req-legacy"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, req_id, prompt="hi", domain="")
    update_request_meta(run_id, req_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    _emit_cached(run_id, req_id, module="policy", action="generate", model="gpt-4o", prompt=200, completion=100, cached=None)
    resp = ui_client.get(f"/runs/{run_id}/requests/{req_id}")
    assert resp.status_code == 200, resp.text
    assert "% cached" not in resp.text

"""Token visibility on the request-detail page: per-module badges + domain retrieval.

Covers:
  * ``_domain_retrieval_view`` and the token rollup added to ``_module_summaries``
    (pure helpers).
  * End-to-end render of ``/runs/{run_id}/requests/{request_id}`` asserting the
    per-call token badge, the per-model token panel, and the Domain retrieval
    section all appear for seeded ``llm_calls`` rows.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from moralstack.observability.events import EVENT_LLM_CALL, make_envelope  # noqa: E402
from moralstack.observability.router import route  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import (  # noqa: E402
    create_run,
    init_db,
    update_request_meta,
    upsert_request,
)
from moralstack.ui.app import _domain_retrieval_view, _module_summaries  # noqa: E402


def _bind_db(monkeypatch, dbp: str) -> None:
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")

    def _path() -> str:
        return dbp

    def _mode() -> str:
        return "db_only"

    monkeypatch.setattr("moralstack.observability.config.get_db_path", _path)
    monkeypatch.setattr("moralstack.observability.config.get_observability_mode", _mode)
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_db_path", _path)
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_observability_mode", _mode)
    monkeypatch.setattr("moralstack.observability.router.get_observability_mode", _mode)
    monkeypatch.setattr("moralstack.observability.read_store.get_db_path", _path)


def _usage(prompt: int, completion: int, total: int, source: str = "exact") -> str:
    return json.dumps({"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total, "source": source})


def _emit(run_id, request_id, *, module, action, model, prompt, completion, total, source="exact", summary=None):
    payload = {
        "phase": "constitution_retrieval" if module == "constitution_retriever" else "deliberation",
        "module": module,
        "action": action,
        "model": model,
        "prompt": "p",
        "raw_response": "{}",
        "token_usage_json": _usage(prompt, completion, total, source),
        "billable_provider_call": True,
    }
    if summary is not None:
        payload["parsed_summary_json"] = json.dumps(summary)
    route(make_envelope(EVENT_LLM_CALL, run_id=run_id, request_id=request_id, payload=payload))


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def test_module_summaries_include_token_rollup():
    calls = [
        {"module": "critic", "duration_ms": 10, "total_tokens": 100, "token_usage_estimated": 0, "token_usage_missing": 0},
        {"module": "critic", "duration_ms": 5, "total_tokens": 40, "token_usage_estimated": 1, "token_usage_missing": 0},
    ]
    summaries = _module_summaries(calls)
    assert summaries["critic"]["total_tokens"] == 140
    assert summaries["critic"]["estimated_calls"] == 1
    assert summaries["critic"]["missing_calls"] == 0


def test_domain_retrieval_view_groups_constitution_calls_with_domain():
    calls = [
        {
            "module": "constitution_retriever",
            "action": "enhanced_domain_agent",
            "model": "gpt-4o-mini",
            "total_tokens": 80,
            "input_tokens": 60,
            "output_tokens": 20,
            "duration_ms": 12,
            "parsed_summary_json": '{"domain": "cyber", "retrieval_phase": "risk_routing"}',
        },
        {
            "module": "constitution_retriever",
            "action": "domain_prefilter",
            "model": "gpt-4o-mini",
            "total_tokens": 30,
            "parsed_summary_json": '{"retrieval_phase": "risk_routing"}',
        },
        {"module": "policy", "action": "generate", "model": "gpt-4o", "total_tokens": 500},
    ]
    view = _domain_retrieval_view(calls)
    assert view["has_data"] is True
    assert view["call_count"] == 2  # policy excluded
    assert view["total_tokens"] == 110
    domains = {r["domain"] for r in view["rows"]}
    assert "cyber" in domains


def test_domain_retrieval_view_empty_when_no_constitution_calls():
    view = _domain_retrieval_view([{"module": "policy", "total_tokens": 10}])
    assert view["has_data"] is False
    assert view["rows"] == []


# --------------------------------------------------------------------------- #
# End-to-end render
# --------------------------------------------------------------------------- #


@pytest.fixture()
def ui_client(tmp_path, monkeypatch):
    dbp = str(tmp_path / "ui_tokens.db")
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


def test_request_page_shows_token_badges_and_domain_retrieval(ui_client):
    run_id, req_id = "run-tok", "req-tok"
    create_run(run_id, run_type="single", meta={})
    upsert_request(run_id, req_id, prompt="hi", domain="cyber")
    update_request_meta(run_id, req_id, {"final_action": "NORMAL_COMPLETE", "risk_score": 0.1})
    _emit(run_id, req_id, module="policy", action="generate", model="gpt-4o", prompt=200, completion=100, total=300)
    _emit(run_id, req_id, module="critic", action="critique", model="gpt-4o", prompt=50, completion=10, total=60)
    _emit(
        run_id,
        req_id,
        module="constitution_retriever",
        action="enhanced_domain_agent",
        model="gpt-4o-mini",
        prompt=40,
        completion=8,
        total=48,
        summary={"module": "constitution_retriever", "domain": "cyber", "retrieval_phase": "risk_routing"},
    )

    resp = ui_client.get(f"/runs/{run_id}/requests/{req_id}")
    assert resp.status_code == 200, resp.text
    body = resp.text
    # Per-model token panel + per-call badge unit ("tok").
    assert "Token usage by model" in body
    assert "tok" in body
    # Dedicated domain-retrieval section with the per-domain call.
    assert "Domain retrieval" in body
    assert "enhanced_domain_agent" in body
    assert "cyber" in body

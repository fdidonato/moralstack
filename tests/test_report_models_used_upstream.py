"""
"Models used" summary coverage for the upstream-origin draft model (Codex
round-4): `read_store.py:get_models_used_for_run` + the markdown
`format_models_used_markdown` renderer surface the `upstream_speculative`
draft model as a **distinct row**, and are unchanged in internal mode.
"""

from __future__ import annotations

from moralstack.observability.events import EVENT_LLM_CALL, make_envelope
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.router import route
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
from moralstack.reports.markdown_export import format_models_used_markdown


def _setup(tmp_path, monkeypatch) -> None:
    dbp = str(tmp_path / "report_models_used_upstream.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)


def _emit_llm_call(
    run_id: str, request_id: str, *, module: str, action: str, model: str, call_outcome: str | None = None
) -> None:
    payload: dict[str, object] = {
        "phase": "speculative_generate",
        "module": module,
        "action": action,
        "model": model,
        "prompt": "",
        "raw_response": "",
        "attempts": 1,
    }
    if call_outcome is not None:
        payload["call_outcome"] = call_outcome
    env = make_envelope(EVENT_LLM_CALL, run_id=run_id, request_id=request_id, payload=payload)
    route(env)


class TestReadStoreUpstreamDraftRow:
    def test_upstream_speculative_surfaces_as_distinct_upstream_draft_key(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-mu-upstream-1", run_type="test", meta={})
        upsert_request("run-mu-upstream-1", "req-mu-upstream-1", prompt="p", domain="")

        _emit_llm_call(
            "run-mu-upstream-1", "req-mu-upstream-1", module="policy", action="generate", model="governance-model-G"
        )
        _emit_llm_call(
            "run-mu-upstream-1",
            "req-mu-upstream-1",
            module="upstream_speculative",
            action="generate (speculative)",
            model="client-model-C",
            call_outcome="used",
        )

        rs = SqliteReadStore()
        models = rs.get_models_used_for_run("run-mu-upstream-1")

        assert models.get("policy_generate") == "governance-model-G"
        assert models.get("upstream_draft") == "client-model-C"

    def test_discarded_upstream_speculative_row_excluded(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-mu-upstream-2", run_type="test", meta={})
        upsert_request("run-mu-upstream-2", "req-mu-upstream-2", prompt="p", domain="")

        _emit_llm_call(
            "run-mu-upstream-2",
            "req-mu-upstream-2",
            module="upstream_speculative",
            action="generate (speculative)",
            model="client-model-C",
            call_outcome="discarded",
        )

        rs = SqliteReadStore()
        models = rs.get_models_used_for_run("run-mu-upstream-2")
        assert "upstream_draft" not in models

    def test_internal_mode_run_has_no_upstream_draft_key(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-mu-internal-1", run_type="test", meta={})
        upsert_request("run-mu-internal-1", "req-mu-internal-1", prompt="p", domain="")

        _emit_llm_call(
            "run-mu-internal-1", "req-mu-internal-1", module="policy", action="generate", model="governance-model-G"
        )

        rs = SqliteReadStore()
        models = rs.get_models_used_for_run("run-mu-internal-1")
        assert "upstream_draft" not in models


class TestFormatModelsUsedMarkdownUpstreamRow:
    def test_upstream_draft_row_present_when_set(self) -> None:
        cfg = {
            "baseline": "gpt-4o",
            "judge": "gpt-4o",
            "moralstack": {
                "policy": "governance-model-G",
                "policy_rewrite": "governance-model-G",
                "risk": "governance-model-G",
                "critic": "governance-model-G",
                "simulator": "governance-model-G",
                "hindsight": "governance-model-G",
                "perspectives": "governance-model-G",
                "upstream_draft": "client-model-C",
            },
        }
        md = format_models_used_markdown(cfg, primary_model="governance-model-G")
        assert "MoralStack upstream draft (client model)" in md
        assert "client-model-C" in md

    def test_upstream_draft_row_absent_in_internal_mode(self) -> None:
        cfg = {
            "baseline": "gpt-4o",
            "judge": "gpt-4o",
            "moralstack": {
                "policy": "governance-model-G",
                "policy_rewrite": "governance-model-G",
                "risk": "governance-model-G",
                "critic": "governance-model-G",
                "simulator": "governance-model-G",
                "hindsight": "governance-model-G",
                "perspectives": "governance-model-G",
            },
        }
        md = format_models_used_markdown(cfg, primary_model="governance-model-G")
        assert "MoralStack upstream draft (client model)" not in md
        assert "client-model-C" not in md

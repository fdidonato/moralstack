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
from moralstack.reports.markdown_export import (
    _overlay_db_models,
    _resolve_models_config_for_run,
    format_models_used_markdown,
)


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


class TestFormatModelsUsedMarkdownParallelRisk:
    def test_three_risk_rows_render_when_risk_parallel(self) -> None:
        cfg = {
            "baseline": "gpt-4o",
            "judge": "gpt-4o",
            "moralstack": {
                "policy": "governance-model-G",
                "risk": "governance-model-G",
                "risk_parallel": True,
                "risk_intent": "model-i",
                "risk_signals": "model-s",
                "risk_operational": "model-o",
            },
        }
        md = format_models_used_markdown(cfg, primary_model="governance-model-G")
        assert "| **MoralStack risk · intent** | model-i |" in md
        assert "| **MoralStack risk · signals** | model-s |" in md
        assert "| **MoralStack risk · operational** | model-o |" in md


def _emit_risk_calls(run_id: str, request_id: str, rows: list[tuple[str, str]]) -> None:
    for action, model in rows:
        _emit_llm_call(run_id, request_id, module="risk_estimator", action=action, model=model)


class TestDbPathThreeRiskRows:
    def test_resolve_models_config_for_run_renders_three_risk_rows(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-c4-single", run_type="test", meta={})
        upsert_request("run-c4-single", "req-c4", prompt="p", domain="")
        _emit_llm_call("run-c4-single", "req-c4", module="policy", action="generate", model="gov-G")
        _emit_risk_calls(
            "run-c4-single",
            "req-c4",
            [
                ("estimate_intent", "model-i"),
                ("estimate_signals", "model-s"),
                ("estimate_operational", "model-o"),
            ],
        )

        cfg, primary = _resolve_models_config_for_run("run-c4-single")
        md = format_models_used_markdown(cfg, primary_model=primary)

        assert "| **MoralStack risk** | parallel mini-estimators |" in md
        assert "| **MoralStack risk · intent** | model-i |" in md
        assert "| **MoralStack risk · signals** | model-s |" in md
        assert "| **MoralStack risk · operational** | model-o |" in md

    def test_legacy_run_with_only_risk_renders_single_risk_row_exactly_as_before(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-c4-legacy", run_type="test", meta={})
        upsert_request("run-c4-legacy", "req-c4l", prompt="p", domain="")
        _emit_llm_call("run-c4-legacy", "req-c4l", module="policy", action="generate", model="gov-G")
        _emit_risk_calls("run-c4-legacy", "req-c4l", [("estimate", "legacy-risk-m")])

        cfg, primary = _resolve_models_config_for_run("run-c4-legacy")
        md = format_models_used_markdown(cfg, primary_model=primary)

        # Literal snapshot of the risk line and its neighbours (format as at c9d0e42).
        assert cfg["moralstack"].get("risk_parallel") is None
        assert md == (
            "### Models used\n"
            "\n"
            "| Component | Model |\n"
            "|-----------|-------|\n"
            "| **Baseline** | — |\n"
            "| **Judge** | — |\n"
            "| **MoralStack policy** | gov-G |\n"
            "| **MoralStack policy (rewrite)** | gov-G |\n"
            "| **MoralStack risk** | legacy-risk-m |\n"
            "| **MoralStack critic** | gov-G |\n"
            "| **MoralStack simulator** | gov-G |\n"
            "| **MoralStack hindsight** | gov-G |\n"
            "| **MoralStack perspectives** | gov-G |\n"
        )


class TestOverlayDbModelsRiskMinis:
    def test_overlay_db_models_sets_risk_parallel_from_db(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-c4-bench", run_type="benchmark", meta={})
        upsert_request("run-c4-bench", "req-c4b", prompt="p", domain="")
        _emit_risk_calls(
            "run-c4-bench",
            "req-c4b",
            [("estimate_intent", "db-i"), ("estimate_signals", "db-s")],
        )
        cfg: dict = {"baseline": "gpt-4o", "judge": "gpt-4o", "moralstack": {"policy": "G", "risk": "snap-risk"}}

        _overlay_db_models(cfg, "run-c4-bench")

        ms = cfg["moralstack"]
        assert ms["risk_parallel"] is True
        assert ms["risk_intent"] == "db-i"
        assert ms["risk_signals"] == "db-s"
        assert ms["risk_operational"] == "—"

    def test_overlay_keeps_snapshot_mini_values_the_db_lacks(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-c4-keep", run_type="benchmark", meta={})
        upsert_request("run-c4-keep", "req-c4k", prompt="p", domain="")
        _emit_risk_calls("run-c4-keep", "req-c4k", [("estimate_signals", "db-s")])
        cfg: dict = {
            "baseline": "gpt-4o",
            "judge": "gpt-4o",
            "moralstack": {
                "policy": "G",
                "risk_parallel": True,
                "risk_intent": "snap-i",
                "risk_signals": "snap-s",
                "risk_operational": "snap-o",
            },
        }

        _overlay_db_models(cfg, "run-c4-keep")

        ms = cfg["moralstack"]
        assert ms["risk_intent"] == "snap-i"
        assert ms["risk_signals"] == "db-s"
        assert ms["risk_operational"] == "snap-o"
        assert ms["risk_parallel"] is True

    def test_overlay_with_legacy_db_risk_only_leaves_single_row(self, tmp_path, monkeypatch) -> None:
        _setup(tmp_path, monkeypatch)
        create_run("run-c4-bl", run_type="benchmark", meta={})
        upsert_request("run-c4-bl", "req-c4bl", prompt="p", domain="")
        _emit_risk_calls("run-c4-bl", "req-c4bl", [("estimate", "legacy-m")])
        cfg: dict = {"baseline": "gpt-4o", "judge": "gpt-4o", "moralstack": {"policy": "G", "risk": "snap"}}

        _overlay_db_models(cfg, "run-c4-bl")

        assert cfg["moralstack"]["risk"] == "legacy-m"
        assert "risk_parallel" not in cfg["moralstack"]

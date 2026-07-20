"""
Observability propagation for `generation="upstream_then_verify"` -- single
coherent story (plan Sec. "Test delta").

Upstream delivery: `SPECULATIVE_STARTED` model == client model + `draft_origin=
"upstream"`; speculative + reuse `llm_calls` rows `module="upstream_speculative"`;
`PROXY_OUTPUT_FINALIZED.model` == client model; header
`X-Moralstack-Draft-Origin: upstream`; `requests.meta_json` carries provenance.

Internal mode: no new header, and the persisted/wire payloads (`meta_json`,
`PROXY_OUTPUT_FINALIZED`, llm_calls, SSE/non-stream `model`) never gain the new
provenance keys -- byte-identical to today on every field this test inspects.

E2E, real sqlite (pattern from `test_upstream_then_verify_model_isolation.py`).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import moralstack.observability.service as service_module  # noqa: E402
from moralstack.observability import obs, router  # noqa: E402
from moralstack.observability import request_token_accumulator as rta  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import _get_connection, init_db  # noqa: E402
from moralstack.orchestration.types import OrchestratorConfig as OrchConfig  # noqa: E402
from moralstack.runtime.orchestrator import Orchestrator  # noqa: E402
from moralstack.sdk.config import GovernanceConfig  # noqa: E402
from moralstack.server.proxy import create_app  # noqa: E402
from tests.test_orchestrator import MockRiskEstimator  # noqa: E402


class _GovernancePolicy:
    def __init__(self, model: str, response: str = "GOVERNANCE INTERNAL TEXT (unused on clean path)") -> None:
        self.model = model
        self._response = response

    def _result(self, text: str) -> SimpleNamespace:
        from moralstack.observability.token_usage import TokenUsage

        result = SimpleNamespace(
            text=text,
            tokens_used=10,
            prompt_tokens=6,
            completion_tokens=4,
            token_usage_source="exact",
            prompt_used=None,
            system_used=None,
            finish_reason="stop",
        )
        result.token_usage_json = lambda: TokenUsage.from_generation_result(result).to_json()
        return result

    def generate(self, *args, **kwargs) -> SimpleNamespace:
        return self._result(self._response)

    def generate_messages(self, *args, **kwargs) -> SimpleNamespace:
        return self._result(self._response)

    def rewrite(self, *args, **kwargs) -> SimpleNamespace:
        return self._result(f"REVISED: {self._response}")


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    try:
        obs.shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    with rta._lock:
        rta._store.clear()
    yield
    try:
        obs.shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    with rta._lock:
        rta._store.clear()


def _setup_db(tmp_path, monkeypatch) -> str:
    dbp = str(tmp_path / "upstream_observability_e2e.db")
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def _upstream_client(content: str) -> MagicMock:
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = MagicMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5),
        )
    )
    return mock_openai


def _make_app(orchestrator, openai_client, monkeypatch, *, config: GovernanceConfig):
    async def _run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("moralstack.server.proxy.run_in_threadpool", _run_in_threadpool)
    return create_app(openai_client=openai_client, orchestrator=orchestrator, config=config)


def _post(app, *, model: str, content: str, conv_id: str) -> tuple[dict, dict]:
    async def _do():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": content}]},
                headers={"X-Moralstack-Conversation-Id": conv_id},
            )
            return resp

    resp = asyncio.run(_do())
    assert resp.status_code == 200
    return resp.json(), dict(resp.headers)


def _latest_run_and_request(dbp: str) -> tuple[str, str]:
    conn = _get_connection(dbp)
    try:
        run_row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        req_row = conn.execute(
            "SELECT request_id FROM requests WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        return run_row["run_id"], req_row["request_id"]
    finally:
        conn.close()


def _orchestration_events(dbp: str, run_id: str, request_id: str) -> list[sqlite3.Row]:
    obs.flush(timeout=10.0)
    conn = _get_connection(dbp)
    try:
        return conn.execute(
            "SELECT event_type, payload_json FROM orchestration_events WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        ).fetchall()
    finally:
        conn.close()


def _llm_calls(dbp: str, run_id: str, request_id: str) -> list[sqlite3.Row]:
    obs.flush(timeout=10.0)
    conn = _get_connection(dbp)
    try:
        return conn.execute(
            "SELECT module, model, call_outcome FROM llm_calls WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        ).fetchall()
    finally:
        conn.close()


def _request_meta_json(dbp: str, run_id: str, request_id: str) -> dict:
    obs.flush(timeout=10.0)
    conn = _get_connection(dbp)
    try:
        row = conn.execute(
            "SELECT meta_json FROM requests WHERE run_id = ? AND request_id = ?",
            (run_id, request_id),
        ).fetchone()
        return json.loads(row["meta_json"]) if row and row["meta_json"] else {}
    finally:
        conn.close()


class TestUpstreamObservabilityPropagation:
    def test_upstream_delivery_propagates_provenance_end_to_end(self, tmp_path, monkeypatch) -> None:
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=False,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
        )
        openai_client = _upstream_client("CLIENT UPSTREAM ANSWER")
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(generation="upstream_then_verify"))

        body, headers = _post(app, model="client-model-C", content="hello weather", conv_id="obs-conv-1")
        assert body["choices"][0]["message"]["content"] == "CLIENT UPSTREAM ANSWER"
        assert body["model"] == "client-model-C"
        assert headers.get("x-moralstack-draft-origin") == "upstream"
        assert headers.get("x-moralstack-draft-model") == "client-model-C"

        run_id, request_id = _latest_run_and_request(dbp)

        events = _orchestration_events(dbp, run_id, request_id)
        spec_started = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "SPECULATIVE_STARTED"]
        assert spec_started, "expected a SPECULATIVE_STARTED event"
        assert spec_started[0].get("model") == "client-model-C"
        assert spec_started[0].get("draft_origin") == "upstream"

        finalized = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "PROXY_OUTPUT_FINALIZED"]
        assert finalized, "expected a PROXY_OUTPUT_FINALIZED event"
        assert finalized[0].get("model") == "client-model-C"

        rows = _llm_calls(dbp, run_id, request_id)
        speculative_rows = [r for r in rows if r["module"] == "upstream_speculative"]
        assert speculative_rows, f"expected upstream_speculative llm_calls rows, got: {[dict(r) for r in rows]}"
        assert all(r["model"] == "client-model-C" for r in speculative_rows)

        meta = _request_meta_json(dbp, run_id, request_id)
        assert meta.get("draft_origin") == "upstream"
        assert meta.get("draft_model") == "client-model-C"

    def test_internal_mode_never_adds_provenance_keys(self, tmp_path, monkeypatch) -> None:
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=False,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
        )
        openai_client = MagicMock()
        openai_client.chat.completions.create = MagicMock(side_effect=AssertionError("wrapped client must not be called"))
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig())

        body, headers = _post(app, model="ignored-alias-model", content="hello weather", conv_id="obs-conv-internal-1")
        assert body["choices"][0]["message"]["content"]
        assert "x-moralstack-draft-origin" not in {k.lower() for k in headers}
        assert "x-moralstack-draft-model" not in {k.lower() for k in headers}

        run_id, request_id = _latest_run_and_request(dbp)

        events = _orchestration_events(dbp, run_id, request_id)
        spec_started = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "SPECULATIVE_STARTED"]
        assert spec_started, "expected a SPECULATIVE_STARTED event"
        assert "draft_origin" not in spec_started[0]

        finalized = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "PROXY_OUTPUT_FINALIZED"]
        assert finalized, "expected a PROXY_OUTPUT_FINALIZED event"
        assert "draft_origin" not in finalized[0]
        assert "draft_model" not in finalized[0]

        rows = _llm_calls(dbp, run_id, request_id)
        assert not any(r["module"] == "upstream_speculative" for r in rows)

        meta = _request_meta_json(dbp, run_id, request_id)
        assert "draft_origin" not in meta
        assert "draft_model" not in meta

"""End-to-end token accounting integration tests."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import moralstack.observability.service as service_module  # noqa: E402
from moralstack.observability import obs, router  # noqa: E402
from moralstack.observability import request_token_accumulator as rta  # noqa: E402
from moralstack.observability.context import set_current_request_id, set_current_run_id  # noqa: E402
from moralstack.observability.read_store import SqliteReadStore  # noqa: E402
from moralstack.observability.service import get_obs  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import _get_connection, create_run, init_db, upsert_request  # noqa: E402
from moralstack.observability.write_queue import ObservabilityWriteQueue  # noqa: E402
from moralstack.orchestration.types import OrchestratorConfig as OrchConfig  # noqa: E402
from moralstack.runtime.orchestrator import Orchestrator  # noqa: E402
from moralstack.sdk.config import GovernanceConfig  # noqa: E402
from moralstack.server.proxy import create_app  # noqa: E402
from tests.test_orchestrator import MockRiskEstimator  # noqa: E402


@dataclass
class TokenGenerationResult:
    text: str
    tokens_used: int = 50
    prompt_tokens: int = 30
    completion_tokens: int = 20
    token_usage_source: str = "exact"
    finish_reason: str = "stop"

    def token_usage_json(self) -> str | None:
        from moralstack.observability.token_usage import TokenUsage

        return TokenUsage.from_generation_result(self).to_json()


def _token_orchestrator(policy=None, risk_estimator=None) -> Orchestrator:
    """Minimal orchestrator with speculative overlap off for deterministic token accounting."""
    config = OrchConfig(
        max_deliberation_cycles=2,
        enable_perspectives=False,
        enable_simulation=False,
        enable_hindsight=False,
        enable_speculative_generation=False,
    )
    return Orchestrator(
        config=config,
        policy=policy or TokenPolicyLLM(),
        risk_estimator=risk_estimator or MockRiskEstimator(),
    )


class TokenPolicyLLM:
    def __init__(self, response: str = "This is a helpful response."):
        self.model = "gpt-test"
        self.default_response = response
        self.call_count = 0

    def generate(self, *args, **kwargs) -> TokenGenerationResult:
        self.call_count += 1
        source = "estimated" if self.call_count % 2 == 0 else "exact"
        return TokenGenerationResult(text=self.default_response, token_usage_source=source)

    def rewrite(self, *args, **kwargs) -> TokenGenerationResult:
        return self.generate(*args, **kwargs)


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
    dbp = str(tmp_path / "token_e2e.db")
    # MORALSTACK_OBSERVABILITY_DB_PATH takes precedence over the legacy
    # MORALSTACK_DB_PATH in get_db_path(); some tests exercise the real
    # load_env()/dotenv path with override=True, which can leak the .env
    # value for the rest of the session. Clear it explicitly so this test
    # stays isolated regardless of test order.
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    init_db(dbp)
    return dbp


def _sql_billable_totals(conn: sqlite3.Connection, run_id: str, request_id: str) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM llm_calls
        WHERE run_id = ? AND request_id = ?
          AND COALESCE(billable_provider_call, 1) = 1
        """,
        (run_id, request_id),
    ).fetchone()
    return {
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
        "total_tokens": int(row["total_tokens"]),
    }


def _make_proxy_app(orchestrator, monkeypatch):
    mock_openai = MagicMock()
    mock_openai.chat.completions.create = MagicMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="upstream"))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )
    )

    async def _run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("moralstack.server.proxy.run_in_threadpool", _run_in_threadpool)
    return create_app(openai_client=mock_openai, orchestrator=orchestrator, config=GovernanceConfig())


def test_process_to_proxy_usage_end_to_end(tmp_path, monkeypatch):
    dbp = _setup_db(tmp_path, monkeypatch)
    orch = _token_orchestrator()
    app = _make_proxy_app(orch, monkeypatch)

    async def _post():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello weather"}]},
                headers={"X-Moralstack-Conversation-Id": "e2e-conv-1"},
            )

    resp = asyncio.run(_post())
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["total_tokens"] > 0

    obs.flush(timeout=10.0)

    conn = _get_connection(dbp)
    run_row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    req_row = conn.execute(
        "SELECT request_id FROM requests WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
        (run_row["run_id"],),
    ).fetchone()
    run_id = run_row["run_id"]
    request_id = req_row["request_id"]

    sql_totals = _sql_billable_totals(conn, run_id, request_id)
    assert sql_totals["total_tokens"] == usage["total_tokens"]

    rs = SqliteReadStore()
    store_totals = rs.get_token_usage_totals(run_id, request_id)
    assert store_totals is not None
    assert store_totals["total_tokens"] == usage["total_tokens"]

    breakdown = rs.get_token_usage_breakdown(run_id, request_id)
    modules = {row["module"] for row in breakdown}
    assert "policy" in modules

    finalized = conn.execute(
        "SELECT COUNT(*) AS c FROM request_token_usage WHERE run_id = ? AND request_id = ?",
        (run_id, request_id),
    ).fetchone()["c"]
    assert finalized == 1
    conn.close()


def test_process_to_proxy_usage_survives_write_queue_drop(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    orch = _token_orchestrator()
    app = _make_proxy_app(orch, monkeypatch)

    svc = get_obs()
    svc._queue.shutdown(timeout=1.0)
    svc._queue = ObservabilityWriteQueue(maxsize=1)

    async def _post():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello weather"}]},
                headers={"X-Moralstack-Conversation-Id": "e2e-drop-conv"},
            )

    resp = asyncio.run(_post())
    assert resp.status_code == 200
    usage = resp.json()["usage"]
    assert usage["total_tokens"] > 0

    obs.flush(timeout=2.0)
    assert svc._queue.stats()["dropped_count"] >= 1


def test_multiturn_usage_accumulates_per_turn_not_across_turns(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)
    orch = _token_orchestrator()
    app = _make_proxy_app(orch, monkeypatch)

    async def _two_turns():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post(
                "/v1/chat/completions",
                json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hello weather turn1"}]},
                headers={"X-Moralstack-Conversation-Id": "multiturn-tokens"},
            )
            r2 = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o",
                    "messages": [
                        {"role": "user", "content": "hello weather turn1"},
                        {"role": "assistant", "content": "ok"},
                        {"role": "user", "content": "hello weather turn2"},
                    ],
                },
                headers={"X-Moralstack-Conversation-Id": "multiturn-tokens"},
            )
            return r1, r2

    r1, r2 = asyncio.run(_two_turns())
    u1 = r1.json()["usage"]["total_tokens"]
    u2 = r2.json()["usage"]["total_tokens"]
    assert u1 > 0
    assert u2 > 0
    assert u2 == u1


def test_concurrent_conversations_usage_not_mixed(tmp_path, monkeypatch):
    _setup_db(tmp_path, monkeypatch)

    class IndexedTokenPolicy(TokenPolicyLLM):
        def generate(self, *args, **kwargs) -> TokenGenerationResult:
            prompt = kwargs.get("prompt", args[0] if args else "")
            idx = 0
            for part in str(prompt).split():
                if part.isdigit():
                    idx = int(part)
            total = 50 + idx * 10
            return TokenGenerationResult(
                text=self.default_response,
                tokens_used=total,
                prompt_tokens=total - 10,
                completion_tokens=10,
                token_usage_source="exact",
            )

    orch = _token_orchestrator(policy=IndexedTokenPolicy())
    app = _make_proxy_app(orch, monkeypatch)
    n = 5

    async def _run():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:

            async def one(i: int):
                r = await client.post(
                    "/v1/chat/completions",
                    json={"model": "gpt-4o", "messages": [{"role": "user", "content": f"hello weather {i}"}]},
                    headers={"X-Moralstack-Conversation-Id": f"conv-tok-{i}"},
                )
                return i, r.json()["usage"]["total_tokens"]

            return await asyncio.gather(*(one(i) for i in range(n)))

    results = asyncio.run(_run())
    by_index = dict(results)
    for i in range(n):
        assert by_index[i] == 50 + i * 10


def test_billable_provider_call_parity_between_accumulator_and_sql_reconstruction(tmp_path, monkeypatch):
    import moralstack.observability.request_token_accumulator as acc
    from moralstack.observability.events import EVENT_LLM_CALL, make_envelope
    from moralstack.observability.request_token_accumulator import finalize_and_persist

    _setup_db(tmp_path, monkeypatch)
    create_run("run-parity", run_type="test", meta={})
    upsert_request("run-parity", "req-parity", prompt="hi", domain="")
    with acc._lock:
        acc._store.clear()

    set_current_run_id("run-parity")
    set_current_request_id("req-parity")

    def _usage(total: int) -> str:
        return json.dumps(
            {
                "prompt_tokens": total,
                "completion_tokens": 0,
                "total_tokens": total,
                "source": "exact",
            }
        )

    envelopes = [
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-parity",
            request_id="req-parity",
            payload={"module": "policy", "token_usage_json": _usage(10), "billable_provider_call": True},
        ),
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-parity",
            request_id="req-parity",
            payload={"module": "policy", "token_usage_json": _usage(20), "billable_provider_call": True},
        ),
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-parity",
            request_id="req-parity",
            payload={"module": "diag", "token_usage_json": _usage(9999), "billable_provider_call": False},
        ),
        make_envelope(
            EVENT_LLM_CALL,
            run_id="run-parity",
            request_id="req-parity",
            payload={"module": "diag", "token_usage_json": _usage(8888), "billable_provider_call": False},
        ),
    ]
    for env in envelopes:
        obs.emit(env)
    obs.flush(timeout=5.0)

    sync_totals = finalize_and_persist("run-parity", "req-parity")
    assert sync_totals is not None
    assert sync_totals.total_tokens == 30

    conn = _get_connection(str(tmp_path / "token_e2e.db"))
    sql_totals = _sql_billable_totals(conn, "run-parity", "req-parity")
    assert sql_totals["total_tokens"] == 30

    obs.flush(timeout=5.0)
    rs = SqliteReadStore()
    store_totals = rs.get_token_usage_totals("run-parity", "req-parity")
    assert store_totals is not None
    assert store_totals["total_tokens"] == 30
    conn.close()

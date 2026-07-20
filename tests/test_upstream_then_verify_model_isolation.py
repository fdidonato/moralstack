"""E2E client-model isolation lock for `generation="upstream_then_verify"`.

Real sqlite (pattern from `test_token_accounting_e2e.py`). Runs a full request
through the proxy with a real `Orchestrator` (speculative overlap ON) and an
upstream/client model distinct from the governance model, then asserts on the
persisted `llm_calls` rows: every row with `module != 'upstream_speculative'`
carries the governance model; exactly one row has `module ==
'upstream_speculative'` and the client model.

Assertion is primarily on `module` (not only `model`) so the degenerate case
`client-model == governance-model` is still detectable -- covered by a second
test that uses the SAME model string for both and relies on `module` alone to
distinguish the speculative row from internal rows.
"""

from __future__ import annotations

import asyncio
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
    """Real-shaped policy double: `token_usage_json()` implemented (unlike
    `MockPolicyLLM`, which some code paths under test call unconditionally)."""

    def __init__(self, model: str, response: str = "GOVERNANCE INTERNAL TEXT (unused on the clean path)") -> None:
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
    dbp = str(tmp_path / "upstream_isolation_e2e.db")
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


def _make_app(orchestrator, openai_client, monkeypatch):
    async def _run_in_threadpool(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    monkeypatch.setattr("moralstack.server.proxy.run_in_threadpool", _run_in_threadpool)
    return create_app(
        openai_client=openai_client,
        orchestrator=orchestrator,
        config=GovernanceConfig(generation="upstream_then_verify"),
    )


def _post(app, *, model: str, content: str, conv_id: str) -> tuple[dict, str]:
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
    return resp.json(), conv_id


def _fetch_llm_call_rows(dbp: str) -> list[sqlite3.Row]:
    obs.flush(timeout=10.0)
    conn = _get_connection(dbp)
    try:
        run_row = conn.execute("SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        req_row = conn.execute(
            "SELECT request_id FROM requests WHERE run_id = ? ORDER BY rowid DESC LIMIT 1",
            (run_row["run_id"],),
        ).fetchone()
        rows = conn.execute(
            "SELECT module, model, call_outcome FROM llm_calls WHERE run_id = ? AND request_id = ?",
            (run_row["run_id"], req_row["request_id"]),
        ).fetchall()
    finally:
        conn.close()
    return rows


class TestClientModelIsolation:
    def test_upstream_speculative_row_isolated_from_governance_rows(self, tmp_path, monkeypatch):
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
        openai_client = _upstream_client("CLIENT DRAFT ANSWER (client-model-C)")
        app = _make_app(orch, openai_client, monkeypatch)

        body, _ = _post(app, model="client-model-C", content="hello weather", conv_id="iso-conv-1")
        assert body["choices"][0]["message"]["content"] == "CLIENT DRAFT ANSWER (client-model-C)"
        assert body["model"] == "client-model-C"

        rows = _fetch_llm_call_rows(dbp)
        assert rows, "expected llm_calls rows to be persisted"

        non_speculative = [r for r in rows if r["module"] != "upstream_speculative"]
        speculative = [r for r in rows if r["module"] == "upstream_speculative"]

        for r in non_speculative:
            assert (
                r["model"] == "governance-model-G"
            ), f"non-speculative row leaked the client model: module={r['module']!r} model={r['model']!r}"

        assert len(speculative) == 1, f"expected exactly one upstream_speculative row, got {len(speculative)}"
        assert speculative[0]["model"] == "client-model-C"
        assert speculative[0]["call_outcome"] == "used"

        assert all(r["model"] is not None and r["model"] != "" for r in rows), "no row should have a NULL/empty model"

    def test_degenerate_same_model_string_still_isolated_by_module(self, tmp_path, monkeypatch):
        """When the client model string equals the governance model string,
        `model` alone cannot distinguish the rows -- `module` must."""
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=False,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("same-model-X"),
            risk_estimator=MockRiskEstimator(),
        )
        openai_client = _upstream_client("CLIENT DRAFT ANSWER (same model string)")
        app = _make_app(orch, openai_client, monkeypatch)

        # A morally-nuanced prompt routes through deliberation (not the benign
        # fast-path), so the cycle-1 reuse row (a *separate* `llm_call`, distinct
        # from the speculative row itself) is also emitted -- exercising the
        # module-vs-model distinction on more than a single row.
        body, _ = _post(app, model="same-model-X", content="is it ok to lie about ethics", conv_id="iso-conv-2")
        assert body["choices"][0]["message"]["content"] == "CLIENT DRAFT ANSWER (same model string)"

        rows = _fetch_llm_call_rows(dbp)
        assert rows

        # Every row (speculative or not) shares the same `model` string in
        # this degenerate case -- the isolation lock must key on `module`.
        assert {r["model"] for r in rows} == {"same-model-X"}

        speculative_or_reuse = [r for r in rows if r["module"] == "upstream_speculative"]
        # The speculative row itself, PLUS the deliberative cycle-1 reuse row
        # that delivered it -- both must carry the distinct module label even
        # though `model` cannot discriminate them from an internal-only run.
        assert len(speculative_or_reuse) == 2, f"expected 2 upstream_speculative-labeled rows, got {rows}"

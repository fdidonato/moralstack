"""
Regression for the blocking bug found by Codex diff review (round after
`ai/reviews/codex-diff-review-upstream-then-verify-generation-20260720-131500.md`):
`_soft_revision_pass` (`moralstack/orchestration/deliberation_runner.py`)
overwrote `state.draft_response` with a governance rewrite but never cleared
`state._draft_verbatim_reuse`, so `ResponseAssembler._apply_draft_provenance`
kept stamping the *governance-generated* rewrite as an *upstream verbatim*
draft -- audit-trail corruption on the exact fact this feature exists to make
traceable.

Two layers:
  - `TestSoftRevisionPassClearsVerbatimReuseFlag`: direct unit-level
    regression on `DeliberationRunner._soft_revision_pass` -- the precise
    mechanism that was broken.
  - `TestSoftRevisionEndToEndReportsGovernanceProvenance`: full pipeline via
    the proxy (real `Orchestrator`, real sqlite) -- upstream draft reused at
    cycle 1, `CONVERGED_WITH_SUGGESTIONS`, soft revision fires. Asserts the
    delivered content is the governance rewrite (never the upstream draft),
    `draft_origin` stays "internal", no upstream-draft header/meta_json keys,
    and proxy/SSE `model` + `PROXY_OUTPUT_FINALIZED.model` report the
    governance model.

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from moralstack.orchestration.deliberation_runner import DeliberationRunner
from moralstack.orchestration.response_assembler import ResponseAssembler
from moralstack.orchestration.types import (
    DeliberationDependencies,
    DeliberationState,
    OrchestratorConfig,
    ProcessedRequest,
)
from moralstack.utils.output_protection import OutputProtector
from tests.test_orchestrator import MockCritic, MockRiskEstimator

_UPSTREAM_DRAFT = "CLIENT-MODEL-C VERBATIM UPSTREAM DRAFT (must not survive soft revision)"
_GOVERNANCE_REWRITE = "REVISED-BY-GOVERNANCE: improved, more balanced answer"


class _RewritingPolicy:
    """Minimal real-shaped policy double: only `.rewrite()` matters here."""

    def __init__(self, model: str, rewrite_text: str) -> None:
        self.model = model
        self._rewrite_text = rewrite_text

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

    def rewrite(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return self._result(self._rewrite_text)


class TestSoftRevisionPassClearsVerbatimReuseFlag:
    """Direct regression on the exact mechanism Codex flagged: a
    `_soft_revision_pass` overwrite of `state.draft_response` must clear
    `_draft_verbatim_reuse`, mirroring the guard `_generate_or_revise`
    already applies at its own rewrite/generate sites."""

    def _build_runner(self, policy: Any) -> DeliberationRunner:
        deps = DeliberationDependencies(
            policy=policy,
            critic=None,
            simulator=None,
            hindsight=None,
            perspectives=None,
            constitution_store=None,
            output_protector=OutputProtector(),
        )
        return DeliberationRunner(
            OrchestratorConfig(),
            deps,
            "system",
            None,
            MagicMock(),
        )

    def test_soft_revision_rewrite_clears_verbatim_reuse_flag(self) -> None:
        runner = self._build_runner(_RewritingPolicy("governance-model-G", _GOVERNANCE_REWRITE))
        request = ProcessedRequest(prompt="p", request_id="req-soft-rev-unit-1")
        state = DeliberationState(cycle=1, draft_response=_UPSTREAM_DRAFT)
        # Mirrors the deliberative cycle-1 reuse of an upstream speculative
        # draft (`_generate_or_revise` at deliberation_runner.py:2725).
        state._draft_verbatim_reuse = True
        # A pending soft suggestion so `build_aggregated_guidance` is non-empty
        # and the rewrite actually fires (otherwise `_soft_revision_pass` is a
        # no-op and the bug can't manifest).
        state.hindsight = SimpleNamespace(
            aggregated=SimpleNamespace(expected_value=0.5, recommendation="proceed"),
            feedback="Add a concrete example and note limitations.",
        )

        result_state = runner._soft_revision_pass(state, request, risk_estimation=None)

        assert result_state.draft_response == _GOVERNANCE_REWRITE
        assert result_state.draft_response != _UPSTREAM_DRAFT
        assert result_state.soft_revision_applied is True
        # THE FIX: a governance rewrite is no longer the verbatim upstream
        # draft, regardless of how it got there.
        assert result_state._draft_verbatim_reuse is False

    def test_soft_revision_rewrite_provenance_not_applied_by_response_assembler(self) -> None:
        """End of the chain: with the flag correctly cleared,
        `ResponseAssembler._apply_draft_provenance` must NOT stamp upstream
        provenance onto the governance-rewritten draft."""
        from moralstack.orchestration.types import DraftProvenance, ResponseMetadata

        runner = self._build_runner(_RewritingPolicy("governance-model-G", _GOVERNANCE_REWRITE))
        request = ProcessedRequest(prompt="p", request_id="req-soft-rev-unit-2")
        state = DeliberationState(cycle=1, draft_response=_UPSTREAM_DRAFT)
        state._draft_verbatim_reuse = True
        state.hindsight = SimpleNamespace(
            aggregated=SimpleNamespace(expected_value=0.5, recommendation="proceed"),
            feedback="Add a concrete example and note limitations.",
        )
        state = runner._soft_revision_pass(state, request, risk_estimation=None)

        metadata = ResponseMetadata(processing_time_ms=0)
        ResponseAssembler._apply_draft_provenance(
            metadata,
            state,
            DraftProvenance(origin="upstream", model="client-model-C"),
        )

        assert metadata.draft_origin == "internal"
        assert metadata.draft_model == ""
        assert metadata.internal_draft_reused is False


# =============================================================================
# End-to-end: real Orchestrator through the proxy, real sqlite.
# =============================================================================

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


class _GovernancePolicy:
    """Real-shaped policy double covering the full deliberative surface used
    by this scenario (`generate`/`generate_messages`/`rewrite`)."""

    def __init__(self, model: str) -> None:
        self.model = model

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

    def generate(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return self._result("GOVERNANCE INTERNAL TEXT (unused on the reused-draft path)")

    def generate_messages(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return self._result("GOVERNANCE INTERNAL TEXT (unused on the reused-draft path)")

    def rewrite(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return self._result(_GOVERNANCE_REWRITE)


class _FakePerspectivesModule:
    """Clean, high-but-not-perfect approval -- eligible for cycle-1 early
    convergence (>= 0.78 weighted / per-perspective) without being excluded
    from the soft-revision gate (< 0.95)."""

    def evaluate(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        perspective = SimpleNamespace(
            perspective_id="user",
            perspective_name="User",
            approval_score=0.8,
            concerns=[],
            suggestions=[],
            rationale="",
        )
        aggregation = SimpleNamespace(recommendation="proceed", weighted_approval=0.8, min_approval=0.8)
        return SimpleNamespace(
            results=[perspective],
            aggregation=aggregation,
            raw_responses=[],
            prompts=[],
            system_prompts=[],
        )


class _FakeConstitutionStore:
    """Minimal constitution store: enough for `DeliberationRunner._critique`
    to run (it no-ops when both `constitution_store` and `constitution` are
    None) without pulling in the real YAML-backed store. Accepts and ignores
    any keyword arguments the real retrieval call sites pass."""

    def get_constitution(self, domain: str | None = None) -> SimpleNamespace:
        return SimpleNamespace(principles=[])

    def get_relevant_principles(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class _FakeHindsightModule:
    """Below-0.7 expected value with a non-empty `feedback` -- exactly one
    pending soft suggestion (`soft_revision_min_suggestions` default is 1),
    `recommendation="proceed"` so it never blocks cycle-1 early convergence
    or contributes a refuse/revise vote."""

    def evaluate(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            aggregated=SimpleNamespace(expected_value=0.5, recommendation="proceed"),
            feedback="Add a concrete example and note limitations.",
            suggestions=[],
        )


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
    dbp = str(tmp_path / "upstream_soft_revision_e2e.db")
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


class TestSoftRevisionEndToEndReportsGovernanceProvenance:
    def test_soft_revision_after_upstream_reuse_reports_governance_everywhere(self, tmp_path, monkeypatch) -> None:
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=True,
                enable_simulation=False,
                enable_hindsight=True,
                # Run hindsight on cycle 1 too (default gating only runs it on
                # the final cycle) so the pending-suggestion signal that
                # triggers soft revision is available at cycle-1 early
                # convergence.
                enable_hindsight_gating=False,
                enable_speculative_generation=True,
                enable_soft_revision=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
            perspectives=_FakePerspectivesModule(),
            hindsight=_FakeHindsightModule(),
            constitution_store=_FakeConstitutionStore(),
        )
        openai_client = _upstream_client(_UPSTREAM_DRAFT)
        app = _make_app(
            orch,
            openai_client,
            monkeypatch,
            config=GovernanceConfig(model="governance-model-G", generation="upstream_then_verify"),
        )

        # "is it ok to lie about ethics" -> MORALLY_NUANCED / DELIBERATE
        # (MockRiskEstimator), routes through deliberation rather than the
        # benign fast-path, so the cycle-1 reuse + soft-revision machinery
        # under test actually runs.
        body, headers = _post(app, model="client-model-C", content="is it ok to lie about ethics", conv_id="soft-rev-e2e-1")

        delivered = body["choices"][0]["message"]["content"]
        assert delivered == _GOVERNANCE_REWRITE, f"expected the governance rewrite to be delivered, got: {delivered!r}"
        assert _UPSTREAM_DRAFT not in delivered

        # THE FIX, at the proxy surface: model attribution reports the
        # governance model, never the client/upstream model, and no upstream
        # provenance header survives a revised delivery.
        assert body["model"] == "governance-model-G"
        assert "x-moralstack-draft-origin" not in {k.lower() for k in headers}
        assert "x-moralstack-draft-model" not in {k.lower() for k in headers}

        run_id, request_id = _latest_run_and_request(dbp)

        events = _orchestration_events(dbp, run_id, request_id)
        finalized = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "PROXY_OUTPUT_FINALIZED"]
        assert finalized, "expected a PROXY_OUTPUT_FINALIZED event"
        assert finalized[0].get("model") == "governance-model-G"
        assert "draft_origin" not in finalized[0]
        assert "draft_model" not in finalized[0]

        meta = _request_meta_json(dbp, run_id, request_id)
        assert "draft_origin" not in meta
        assert "draft_model" not in meta

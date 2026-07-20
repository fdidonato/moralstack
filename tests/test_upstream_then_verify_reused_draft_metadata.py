"""
Reused-draft final-metadata attribution for `generation="upstream_then_verify"`
(Codex round-2 + round-3 blocking): FAST_PATH *and* DELIBERATIVE unrevised
reuse, both delivered through `ResponseAssembler.assemble` (bypassing the
benign `from_decision` site covered by
`test_upstream_then_verify_benign_provenance.py`).

Also covers the fast-path -> deliberative escalation (quick-check failure)
so a draft carried into deliberation and later delivered unrevised is still
labeled -- and pins Codex Q1: `internal_draft_reused` stays `True` for an
unrevised upstream draft delivered after deliberation (the boolean means "a
draft was reused"; `draft_origin` disambiguates provenance).

Convention: two distinct, recognizable models -- "governance-model-G" (policy)
and "client-model-C" (upstream draft) -- never sharing text.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from moralstack.models.decision_explanation import DecisionExplanation
from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.orchestration.controller import OrchestrationController
from moralstack.orchestration.null_persistence import NullPersistence
from moralstack.orchestration.process_context import ProcessCallContext
from moralstack.orchestration.trace import Trace
from moralstack.orchestration.types import Decision, DraftProvenance, OrchestratorConfig, ProcessedRequest
from moralstack.utils.output_protection import OutputProtector

_DRAFT_TEXT = "CLIENT-MODEL-C VERBATIM REUSE DRAFT"


def _risk_estimation() -> RiskEstimation:
    return RiskEstimation(
        score=0.05,
        confidence=0.9,
        risk_category=RiskCategory.BENIGN,
        operational_risk=OperationalRisk.NONE,
    )


def _fast_path_decision() -> Decision:
    return Decision(
        final_action="NORMAL_COMPLETE",
        path="FAST_PATH",
        intent_clarity="HIGH",
        misuse_plausibility="LOW",
        actionability_risk="LOW",
        triggered_principles=[],
        hard_violations=[],
        risk_signals=[],
        reason_codes=[],
    )


def _build_controller(*, critic: Any = None) -> OrchestrationController:
    return OrchestrationController(
        config=OrchestratorConfig(enable_speculative_generation=True),
        policy=MagicMock(model="governance-model-G"),
        risk_estimator=MagicMock(),
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=None,
        output_protector=OutputProtector(),
        protected_system_prompt="system",
        persistence=NullPersistence(),
    )


class TestFastPathReuseProvenance:
    def test_fast_path_reuse_sets_provenance_and_reuse_row(self) -> None:
        ctrl = _build_controller(critic=None)
        req = ProcessedRequest(prompt="p", request_id="req-fp-reuse-1")

        with patch("moralstack.orchestration.persistence_helpers.async_persist_llm_call") as mock_persist:
            result = ctrl._runner.run_fast_path(
                req,
                _risk_estimation(),
                time.time(),
                decision=_fast_path_decision(),
                speculative_draft=_DRAFT_TEXT,
                draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
            )

        assert result.response.content == _DRAFT_TEXT
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"
        assert result.response.metadata.internal_draft_reused is True

        reuse_calls = [c for c in mock_persist.call_args_list if c.kwargs.get("module") == "upstream_speculative"]
        assert reuse_calls, f"expected an upstream_speculative reuse row, got calls: {mock_persist.call_args_list}"
        assert reuse_calls[0].kwargs.get("model") == "client-model-C"
        assert "speculative-reuse" in str(reuse_calls[0].kwargs.get("action") or "")


class TestDeliberativeUnrevisedReuseProvenance:
    def _call_deliberative(self, ctrl: OrchestrationController, req: ProcessedRequest):
        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="DELIBERATIVE_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id=req.request_id or "",
            final_action="NORMAL_COMPLETE",
            risk_score=0.05,
            risk_category="clearly_benign",
        )
        call_ctx = ProcessCallContext()
        trace = Trace(request_id=req.request_id or "")
        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
        ):
            return ctrl._route_deliberative(
                req,
                _risk_estimation(),
                RiskCategory.BENIGN,
                False,
                False,
                time.time(),
                trace,
                pre_decision=None,
                speculative_draft=_DRAFT_TEXT,
                call_ctx=call_ctx,
                draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
            )

    def test_deliberative_unrevised_reuse_sets_provenance_and_reuse_row(self) -> None:
        ctrl = _build_controller(critic=None)
        req = ProcessedRequest(prompt="p", request_id="req-delib-reuse-1")

        with patch("moralstack.orchestration.persistence_helpers.async_persist_llm_call") as mock_persist:
            result = self._call_deliberative(ctrl, req)

        assert result.response.content == _DRAFT_TEXT
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"
        # Codex Q1: `internal_draft_reused` stays True for an unrevised upstream
        # draft delivered after deliberation -- the boolean means "a draft was
        # reused"; `draft_origin` disambiguates provenance.
        assert result.response.metadata.internal_draft_reused is True

        reuse_calls = [c for c in mock_persist.call_args_list if c.kwargs.get("module") == "upstream_speculative"]
        assert reuse_calls, f"expected an upstream_speculative reuse row, got calls: {mock_persist.call_args_list}"
        assert reuse_calls[0].kwargs.get("model") == "client-model-C"
        assert "speculative-reuse" in str(reuse_calls[0].kwargs.get("action") or "")


class _EscalatingCritic:
    """`quick_check` fails (forces FAST_PATH -> deliberative escalation); the
    subsequent `critique` reports no violations, so the escalated deliberative
    cycle converges unrevised at cycle 1 -- the draft (still upstream-origin,
    still verbatim) is delivered as-is."""

    def quick_check(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(passed=False, critical_violation=None)

    def critique(self, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            violations=[],
            severity_score=0.0,
            has_critical_violations=False,
            revision_guidance="",
            violated_hard=False,
            decision=None,
            skipped=False,
            skip_reason="",
        )


class TestFastPathToDeliberativeEscalationProvenance:
    def test_escalated_unrevised_draft_still_labeled_upstream(self) -> None:
        ctrl = _build_controller(critic=_EscalatingCritic())
        req = ProcessedRequest(prompt="p", request_id="req-escalate-1")

        with patch("moralstack.orchestration.persistence_helpers.async_persist_llm_call") as mock_persist:
            result = ctrl._runner.run_fast_path(
                req,
                _risk_estimation(),
                time.time(),
                decision=_fast_path_decision(),
                constitution=SimpleNamespace(),
                speculative_draft=_DRAFT_TEXT,
                draft_provenance=DraftProvenance(origin="upstream", model="client-model-C"),
            )

        # The escalation delivers the draft unrevised (no violations found):
        # content is still the verbatim upstream draft, and provenance survives
        # the fast-path -> deliberative hop (`_build_deliberative_result`).
        assert result.response.content == _DRAFT_TEXT
        assert result.response.metadata.draft_origin == "upstream"
        assert result.response.metadata.draft_model == "client-model-C"
        assert result.response.metadata.internal_draft_reused is True

        reuse_calls = [c for c in mock_persist.call_args_list if c.kwargs.get("module") == "upstream_speculative"]
        assert (
            reuse_calls
        ), f"expected an upstream_speculative reuse row after escalation, got: {mock_persist.call_args_list}"
        assert reuse_calls[0].kwargs.get("model") == "client-model-C"


# =============================================================================
# Cross-surface (Codex diff-review round): the tests above call the runner
# and controller directly. The plan (`ai/plans/upstream-then-verify-generation.md:692`)
# asks FAST_PATH and deliberative reuse to also be asserted through
# proxy/SSE `model`, `PROXY_OUTPUT_FINALIZED`, headers, `requests.meta_json`,
# and SDK `governance_metadata` -- proving the centralized `assemble`
# provenance set actually reaches every consumer, not just the in-process
# `OrchestratorResult`. Real `Orchestrator` + real sqlite via the proxy
# (pattern from `test_upstream_then_verify_observability.py`); a second,
# lighter SDK-surface check via `GovernedClient` (pattern from
# `test_sdk_upstream_then_verify.py`), both driving the actual route with
# `get_route`/`decide_action` patched only to pick a stable route -- the
# draft-provenance plumbing under test is not patched.
# =============================================================================

import asyncio  # noqa: E402
import json  # noqa: E402
import sqlite3  # noqa: E402

import pytest  # noqa: E402

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from httpx import ASGITransport, AsyncClient  # noqa: E402

import moralstack.observability.service as service_module  # noqa: E402
from moralstack.cli.mocks import (  # noqa: E402
    MockConstitutionStore,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockSimulator,
)
from moralstack.models.risk import RiskPolicyAction  # noqa: E402
from moralstack.observability import obs, router  # noqa: E402
from moralstack.observability import request_token_accumulator as rta  # noqa: E402
from moralstack.observability.sinks.sqlite_sink import _get_connection, init_db  # noqa: E402
from moralstack.orchestration.types import OrchestratorConfig as OrchConfig  # noqa: E402
from moralstack.runtime.orchestrator import Orchestrator, create_orchestrator  # noqa: E402
from moralstack.sdk.config import GovernanceConfig  # noqa: E402
from moralstack.sdk.wrapper import GovernedClient  # noqa: E402
from moralstack.server.proxy import create_app  # noqa: E402
from tests.test_orchestrator import MockCritic, MockRiskEstimator  # noqa: E402

_CROSS_SURFACE_DRAFT = "CLIENT-MODEL-C CROSS-SURFACE REUSE DRAFT"


class _GovernancePolicy:
    """Real-shaped policy double: `token_usage_json()` implemented, matching
    the pattern in `test_upstream_then_verify_model_isolation.py` /
    `test_upstream_then_verify_observability.py`."""

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
        return self._result("GOVERNANCE INTERNAL TEXT (unused on the reused-draft path)")


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
    dbp = str(tmp_path / "upstream_reused_draft_cross_surface_e2e.db")
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
            "SELECT module, model, action, call_outcome FROM llm_calls WHERE run_id = ? AND request_id = ?",
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


def _assert_cross_surface_db_provenance(*, dbp: str) -> None:
    """The DB-side half of the cross-surface assertion, factored out so the
    SSE reuse tests below (which have no single JSON `body`/top-level `model`
    to assert on) can reuse it verbatim."""
    run_id, request_id = _latest_run_and_request(dbp)

    events = _orchestration_events(dbp, run_id, request_id)
    finalized = [json.loads(r["payload_json"]) for r in events if r["event_type"] == "PROXY_OUTPUT_FINALIZED"]
    assert finalized, "expected a PROXY_OUTPUT_FINALIZED event"
    assert finalized[0].get("model") == "client-model-C"

    rows = _llm_calls(dbp, run_id, request_id)
    speculative_rows = [r for r in rows if r["module"] == "upstream_speculative"]
    assert speculative_rows, f"expected upstream_speculative llm_calls rows, got: {[dict(r) for r in rows]}"
    assert all(r["model"] == "client-model-C" for r in speculative_rows)
    # Specifically the *reuse* row (not just the original speculative-draft
    # row, which is always `module="upstream_speculative"` regardless of the
    # reuse-labeling logic under test) must itself carry the label -- this is
    # what pins the plumbing this test class exists to cover.
    reuse_rows = [r for r in rows if "speculative-reuse" in (r["action"] or "")]
    assert reuse_rows, f"expected a speculative-reuse llm_calls row, got: {[dict(r) for r in rows]}"
    assert all(
        r["module"] == "upstream_speculative" for r in reuse_rows
    ), f"reuse row(s) did not carry the upstream_speculative module label: {[dict(r) for r in reuse_rows]}"

    meta = _request_meta_json(dbp, run_id, request_id)
    assert meta.get("draft_origin") == "upstream"
    assert meta.get("draft_model") == "client-model-C"


def _assert_cross_surface_upstream_provenance(*, body: dict, headers: dict, dbp: str, expected_content: str) -> None:
    assert body["choices"][0]["message"]["content"] == expected_content
    assert body["model"] == "client-model-C"
    assert headers.get("x-moralstack-draft-origin") == "upstream"
    assert headers.get("x-moralstack-draft-model") == "client-model-C"
    _assert_cross_surface_db_provenance(dbp=dbp)


def _post_sse(app, *, model: str, content: str, conv_id: str) -> tuple[str, dict, list[str]]:
    """POST with `stream=True` through the real ASGI app and reconstruct the
    synthetic SSE stream. Returns `(reassembled_content, headers, models_seen)`
    where `models_seen` is the `model` field read off of *every*
    `chat.completion.chunk` event (not a single top-level JSON field) --
    proving each streamed chunk, not just a buffered whole, reports the
    expected model. Asserts the response actually streamed (content-type +
    a terminal `[DONE]` event + at least one `chat.completion.chunk`) so a
    regression that silently fell back to the non-stream JSON branch fails
    loudly here rather than passing on a response object that never
    streamed.
    """

    async def _do():
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={"model": model, "stream": True, "messages": [{"role": "user", "content": content}]},
                headers={"X-Moralstack-Conversation-Id": conv_id},
            )
            return resp

    resp = asyncio.run(_do())
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get(
        "content-type", ""
    ), f"expected an SSE stream, got content-type={resp.headers.get('content-type')!r}"

    deltas: list[str] = []
    models_seen: list[str] = []
    saw_done = False
    for line in resp.text.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            saw_done = True
            continue
        chunk = json.loads(data)
        assert chunk["object"] == "chat.completion.chunk"
        models_seen.append(chunk["model"])
        piece = chunk["choices"][0]["delta"].get("content")
        if piece:
            deltas.append(piece)
    assert saw_done, "expected a terminal [DONE] SSE event -- proof this actually streamed, not a bare JSON body"
    assert models_seen, "expected at least one chat.completion.chunk event"
    return "".join(deltas), dict(resp.headers), models_seen


class TestFastPathReuseCrossSurface:
    """FAST_PATH reuse (`deliberation_runner.py:871,895`), asserted through
    the proxy: `body["model"]`, `X-Moralstack-Draft-Origin`/`-Draft-Model`
    headers, `PROXY_OUTPUT_FINALIZED.model`, and `requests.meta_json`.
    `get_route`/`decide_action` are patched only to force the literal
    "fast_path" route (distinct from "benign", which round-5 established is
    not a separate reuse-`llm_call` emitter); the draft-provenance plumbing
    itself runs unpatched.
    """

    def test_fast_path_reuse_reports_upstream_provenance_across_surfaces(self, tmp_path, monkeypatch) -> None:
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
            critic=MockCritic(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(generation="upstream_then_verify"))

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id="",
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch(
                "moralstack.orchestration.controller.get_route",
                return_value=("fast_path", False, RiskPolicyAction.ALLOW),
            ),
        ):
            body, headers = _post(app, model="client-model-C", content="hello weather", conv_id="fp-cross-surface-1")

        _assert_cross_surface_upstream_provenance(body=body, headers=headers, dbp=dbp, expected_content=_CROSS_SURFACE_DRAFT)


class TestDeliberativeUnrevisedReuseCrossSurface:
    """Deliberative cycle-1 reuse, delivered unrevised (`deliberation_runner.py:2647-2673`),
    asserted through the same proxy surfaces. "is it ok to lie about ethics"
    (`MockRiskEstimator`) routes through deliberation without needing a
    route patch; a clean critic + high, suggestion-free perspectives
    converge at cycle 1 with no pending soft suggestions, so the draft is
    delivered verbatim (never revised)."""

    def test_deliberative_unrevised_reuse_reports_upstream_provenance_across_surfaces(self, tmp_path, monkeypatch) -> None:
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=True,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
            perspectives=MockPerspectives(),
            constitution_store=MockConstitutionStore(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(generation="upstream_then_verify"))

        body, headers = _post(
            app, model="client-model-C", content="is it ok to lie about ethics", conv_id="delib-cross-surface-1"
        )

        _assert_cross_surface_upstream_provenance(body=body, headers=headers, dbp=dbp, expected_content=_CROSS_SURFACE_DRAFT)


class TestFastPathReuseCrossSurfaceSSE:
    """`stream=True` counterpart of `TestFastPathReuseCrossSurface`. SSE is
    the second surface upstream text can leave by (the plan mandates buffer
    -> validate -> synthetic SSE replay); no reuse-route SSE coverage
    existed before this test (Codex round-2 review). Asserts every
    `chat.completion.chunk` reports the client model, the reassembled delta
    content equals the upstream draft verbatim, and
    `PROXY_OUTPUT_FINALIZED` / headers / `requests.meta_json` agree with the
    non-stream case above.
    """

    def test_fast_path_reuse_streams_upstream_provenance_across_surfaces(self, tmp_path, monkeypatch) -> None:
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
            critic=MockCritic(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(generation="upstream_then_verify"))

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id="",
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch(
                "moralstack.orchestration.controller.get_route",
                return_value=("fast_path", False, RiskPolicyAction.ALLOW),
            ),
        ):
            content, headers, models_seen = _post_sse(
                app, model="client-model-C", content="hello weather", conv_id="fp-cross-surface-sse-1"
            )

        assert content == _CROSS_SURFACE_DRAFT
        assert all(m == "client-model-C" for m in models_seen)
        assert headers.get("x-moralstack-draft-origin") == "upstream"
        assert headers.get("x-moralstack-draft-model") == "client-model-C"
        _assert_cross_surface_db_provenance(dbp=dbp)


class TestDeliberativeUnrevisedReuseCrossSurfaceSSE:
    """`stream=True` counterpart of `TestDeliberativeUnrevisedReuseCrossSurface`."""

    def test_deliberative_unrevised_reuse_streams_upstream_provenance_across_surfaces(self, tmp_path, monkeypatch) -> None:
        dbp = _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=True,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
            perspectives=MockPerspectives(),
            constitution_store=MockConstitutionStore(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(generation="upstream_then_verify"))

        content, headers, models_seen = _post_sse(
            app, model="client-model-C", content="is it ok to lie about ethics", conv_id="delib-cross-surface-sse-1"
        )

        assert content == _CROSS_SURFACE_DRAFT
        assert all(m == "client-model-C" for m in models_seen)
        assert headers.get("x-moralstack-draft-origin") == "upstream"
        assert headers.get("x-moralstack-draft-model") == "client-model-C"
        _assert_cross_surface_db_provenance(dbp=dbp)


class TestFastPathReuseCrossSurfaceInternalModeSSE:
    """Byte-identity gate (Codex round-2 review item): the identical
    FAST_PATH reuse route, in the default `internal` mode (no client model /
    no `generation="upstream_then_verify"`), streamed via SSE. No
    `upstream_draft_generator` is ever wired in internal mode, so the draft
    is produced by the governance policy; every SSE chunk must keep
    reporting the governance model -- proving the SSE branch's
    `draft_origin`-gated model selection (`server/proxy.py:480-483`) never
    leaks a client model when there is none to leak.
    """

    def test_fast_path_reuse_internal_mode_sse_stays_governance_model(self, tmp_path, monkeypatch) -> None:
        _setup_db(tmp_path, monkeypatch)
        orch = Orchestrator(
            config=OrchConfig(
                enable_perspectives=False,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=_GovernancePolicy("governance-model-G"),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        # Default `internal` mode: generation="internal" (the default), never
        # "upstream_then_verify" -- the wrapped client must never be called.
        app = _make_app(orch, openai_client, monkeypatch, config=GovernanceConfig(model="governance-model-G"))

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id="",
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch(
                "moralstack.orchestration.controller.get_route",
                return_value=("fast_path", False, RiskPolicyAction.ALLOW),
            ),
        ):
            content, headers, models_seen = _post_sse(
                app, model="client-model-C", content="hello weather", conv_id="fp-internal-sse-1"
            )

        openai_client.chat.completions.create.assert_not_called()
        assert "x-moralstack-draft-origin" not in {k.lower() for k in headers}
        assert "x-moralstack-draft-model" not in {k.lower() for k in headers}
        assert models_seen and all(m == "governance-model-G" for m in models_seen)
        assert "client-model-C" not in content
        assert content == "GOVERNANCE INTERNAL TEXT (unused on the reused-draft path)"


class TestReusedDraftSdkMetadataCrossSurface:
    """Same FAST_PATH / deliberative reuse scenarios, asserted at the SDK
    surface (`GovernedClient` / `governance_metadata`) rather than the proxy
    -- the other named consumer in the plan's cross-surface requirement."""

    def _make_mock_orchestrator(self) -> Orchestrator:
        return create_orchestrator(
            policy=MockPolicy(),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
            simulator=MockSimulator(),
            hindsight=MockHindsight(),
            perspectives=MockPerspectives(),
            constitution_store=MockConstitutionStore(),
            max_cycles=1,
            timeout_ms=60_000,
        )

    def test_fast_path_reuse_sdk_metadata_reports_upstream(self) -> None:
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = self._make_mock_orchestrator()
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        # SDK wrapper reads plain OpenAI-shaped objects (mirrors
        # `test_sdk_upstream_then_verify.py::_make_mock_openai_client`), not
        # the raw `SimpleNamespace` double used by the proxy tests above.
        openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=_CROSS_SURFACE_DRAFT, role="assistant"), finish_reason="stop")],
            model="client-model-C",
            usage=MagicMock(total_tokens=5, prompt_tokens=3, completion_tokens=2),
        )
        client = GovernedClient(openai_client, orchestrator, cfg)

        decision = Decision(
            final_action="NORMAL_COMPLETE",
            path="FAST_PATH",
            intent_clarity="HIGH",
            misuse_plausibility="LOW",
            actionability_risk="LOW",
            triggered_principles=[],
            hard_violations=[],
            risk_signals=[],
            reason_codes=[],
        )
        explanation = DecisionExplanation(
            request_id="",
            final_action="NORMAL_COMPLETE",
            risk_score=0.1,
            risk_category="benign",
        )

        with (
            patch("moralstack.orchestration.controller.decide_action", return_value=(decision, explanation)),
            patch("moralstack.orchestration.controller.apply_safe_complete_gating", lambda d, *a, **k: d),
            patch(
                "moralstack.orchestration.controller.get_route",
                return_value=("fast_path", False, RiskPolicyAction.ALLOW),
            ),
        ):
            resp = client.chat.completions.create(
                model="client-model-C",
                messages=[{"role": "user", "content": "What is the speed of light?"}],
            )

        assert resp.content == _CROSS_SURFACE_DRAFT
        assert resp.model == "client-model-C"
        assert resp.governance_metadata.draft_origin == "upstream"
        assert resp.governance_metadata.draft_model == "client-model-C"

    def test_deliberative_reuse_sdk_metadata_reports_upstream(self) -> None:
        """Deliberative cycle-1 reuse (`TestDeliberativeUnrevisedReuseCrossSurface`'s
        proxy scenario) at the SDK surface -- the coverage gap the round-2
        review flagged: SDK metadata coverage in this file was FAST_PATH
        only. Built as a real `Orchestrator` (not `_make_mock_orchestrator`,
        which forces `enable_simulation`/`enable_hindsight` on) so the
        "is it ok to lie about ethics" prompt converges unrevised at cycle 1
        without needing a route patch, mirroring the proxy scenario exactly.
        """
        cfg = GovernanceConfig(model="governance-model-G", generation="upstream_then_verify")
        orchestrator = Orchestrator(
            config=OrchConfig(
                enable_perspectives=True,
                enable_simulation=False,
                enable_hindsight=False,
                enable_speculative_generation=True,
            ),
            policy=MockPolicy(),
            risk_estimator=MockRiskEstimator(),
            critic=MockCritic(),
            perspectives=MockPerspectives(),
            constitution_store=MockConstitutionStore(),
        )
        openai_client = _upstream_client(_CROSS_SURFACE_DRAFT)
        openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=_CROSS_SURFACE_DRAFT, role="assistant"), finish_reason="stop")],
            model="client-model-C",
            usage=MagicMock(total_tokens=5, prompt_tokens=3, completion_tokens=2),
        )
        client = GovernedClient(openai_client, orchestrator, cfg)

        resp = client.chat.completions.create(
            model="client-model-C",
            messages=[{"role": "user", "content": "is it ok to lie about ethics"}],
        )

        assert resp.content == _CROSS_SURFACE_DRAFT
        assert resp.model == "client-model-C"
        assert resp.governance_metadata.draft_origin == "upstream"
        assert resp.governance_metadata.draft_model == "client-model-C"

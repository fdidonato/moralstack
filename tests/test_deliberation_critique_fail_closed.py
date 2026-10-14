"""Pins for ``DeliberationRunner._critique`` (F5 critique fail-closed, phase 0).

Phase 0 is a behavior-preserving extraction: these tests are written and run at HEAD
before the refactor and must stay green after it.

* O13: the critic-call arguments (all three branches), the prev-violations format and
  the ``llm_calls`` diag are byte-identical.
* O13b: a critic exception and the 90% gate are swallowed into ``state.errors`` plus a
  diagnostic ``critique (ERROR)`` row, exactly as before.

Offline and deterministic.
"""

from __future__ import annotations

import time
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
    RequestAnalysisContext,
)

_RUNNER_MOD = "moralstack.orchestration.deliberation_runner"
PROMPT = "the prompt"
DRAFT = "the draft"
CONSTITUTION = SimpleNamespace(name="const")


def _report(**kw: Any) -> SimpleNamespace:
    base: dict[str, Any] = {"violations": [], "revision_guidance": "", "skipped": False}
    base.update(kw)
    return SimpleNamespace(**base)


class _Critic:
    """Critic double recording every call as ``(method, args, kwargs)``."""

    def __init__(self, *, with_principles: bool, store: Any = None, exc: Exception | None = None) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.config = MagicMock(top_k_principles=2)
        self.store = store
        self._exc = exc
        if not with_principles:
            self.critique_with_relevant_principles = None

    def critique(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("critique", args, kwargs))
        if self._exc is not None:
            raise self._exc
        return _report()

    def critique_with_relevant_principles(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
        self.calls.append(("critique_with_relevant_principles", args, kwargs))
        return _report()


def _runner(critic: Any, store: Any = None, timeout_ms: int = 600_000) -> DeliberationRunner:
    policy = MagicMock()
    deps = DeliberationDependencies(
        policy=policy,
        critic=critic,
        simulator=None,
        hindsight=None,
        perspectives=None,
        constitution_store=store,
        output_protector=MagicMock(),
    )
    cfg = OrchestratorConfig(timeout_ms=timeout_ms)
    return DeliberationRunner(cfg, deps, "protected", None, ResponseAssembler(policy=None))


@pytest.fixture
def diags(monkeypatch: pytest.MonkeyPatch) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
    captured: list[tuple[dict[str, Any], dict[str, Any] | None]] = []

    def _record(_logger: Any, diag: Any, persist_kwargs: Any) -> None:
        captured.append((diag, persist_kwargs))

    monkeypatch.setattr(f"{_RUNNER_MOD}.record_llm_call", _record)
    return captured


def _request() -> ProcessedRequest:
    return ProcessedRequest(
        prompt=PROMPT,
        request_id="req-o13",
        developer_contract="DEVCONTRACT",
        conversation_history=[{"role": "user", "content": "hi"}],
        user_context={"domain": "medical"},
    )


def _seeded_state() -> DeliberationState:
    state = DeliberationState(cycle=1)
    state.draft_response = DRAFT
    violations = [SimpleNamespace(principle_id=f"P{i}", rationale=f"{i}" * 100) for i in range(4)]
    state.critiques.append(_report(violations=violations, revision_guidance="PREV-GUIDANCE"))
    return state


EXPECTED_PREV_VIOLATIONS = "; ".join(f"P{i}: {str(i) * 80}" for i in range(3))
EXPECTED_PREV_GUIDANCE = "PREV-GUIDANCE"


def _assert_diag(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    assert len(diags) == 1
    diag, persist_kwargs = diags[0]
    assert diag["action"] == "critique"
    assert diag["prompt"] == f"CRITIQUE\nPrompt: {PROMPT}\nResponse: {DRAFT}"
    assert diag["response"] == "Violations: 0, Guidance: N/A"
    assert persist_kwargs is not None
    assert persist_kwargs["parsed_summary_json"] == "Violations: 0, Guidance: N/A"


def test_o13_critic_call_arguments_byte_identical(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    common_kwargs = {
        "request_id": "req-o13",
        "delib_context": None,
        "previous_violations": EXPECTED_PREV_VIOLATIONS,
        "previous_guidance": EXPECTED_PREV_GUIDANCE,
        "developer_contract": "DEVCONTRACT",
        "conversation_history": [{"role": "user", "content": "hi"}],
    }

    # Branch 1: precomputed request analysis (principles sliced to critic top_k=2).
    p1, p2, p3 = (SimpleNamespace(id=n) for n in ("a", "b", "c"))
    analysis = RequestAnalysisContext(relevant_principles=(p1, p2, p3), constitution=CONSTITUTION, detected_domain="medical")
    critic1 = _Critic(with_principles=True)
    state1 = _seeded_state()
    out1 = _runner(critic1, store=object())._critique(state1, _request(), constitution=None, request_analysis=analysis)
    assert out1 is state1
    assert critic1.calls == [
        (
            "critique",
            (PROMPT, DRAFT, CONSTITUTION),
            {"principles": [p1, p2], **common_kwargs},
        )
    ]
    assert state1.errors == []
    assert len(state1.critiques) == 2
    assert state1._request_analysis_reuse_targets == ["critic"]
    _assert_diag(diags)
    diags.clear()

    # Branch 2: critique_with_relevant_principles with a store.
    critic2 = _Critic(with_principles=True, store=object())
    state2 = _seeded_state()
    _runner(critic2, store=object())._critique(state2, _request(), constitution=None, request_analysis=None)
    assert critic2.calls == [
        (
            "critique_with_relevant_principles",
            (),
            {"request": PROMPT, "response": DRAFT, "domain": "medical", **common_kwargs},
        )
    ]
    assert state2.errors == []
    assert len(state2.critiques) == 2
    _assert_diag(diags)
    diags.clear()

    # Branch 3: plain critique with the constitution passed in.
    critic3 = _Critic(with_principles=False)
    state3 = _seeded_state()
    _runner(critic3)._critique(state3, _request(), constitution=CONSTITUTION, request_analysis=None)
    assert critic3.calls == [("critique", (PROMPT, DRAFT, CONSTITUTION), common_kwargs)]
    assert state3.errors == []
    assert len(state3.critiques) == 2
    _assert_diag(diags)


def test_o13b_critique_error_path_unchanged(diags: list[tuple[dict[str, Any], dict[str, Any] | None]]) -> None:
    critic = _Critic(with_principles=False, exc=RuntimeError("boom"))
    state = DeliberationState(cycle=1)
    state.draft_response = DRAFT
    out = _runner(critic)._critique(state, _request(), constitution=CONSTITUTION)
    assert out is state
    assert state.errors == ["Critique error: boom"]
    assert state.critiques == []
    assert len(diags) == 1
    diag, persist_kwargs = diags[0]
    assert diag["action"] == "critique (ERROR)"
    assert persist_kwargs is None

    # 90% gate: the critic is not called and the timeout is swallowed the same way.
    diags.clear()
    critic2 = _Critic(with_principles=False)
    state2 = DeliberationState(cycle=1)
    state2.draft_response = DRAFT
    _runner(critic2, timeout_ms=600_000)._critique(
        state2, _request(), constitution=CONSTITUTION, start_time=time.time() - 570
    )
    assert critic2.calls == []
    assert state2.errors[0].startswith("Critique error: Timeout approaching before critique")
    assert state2.critiques == []
    assert len(diags) == 1
    assert diags[0][0]["action"] == "critique (ERROR)"
    assert diags[0][1] is None

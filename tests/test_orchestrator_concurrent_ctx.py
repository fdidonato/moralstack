"""
Regression tests for concurrent OrchestrationController.process() calls.

Ensures per-request conversation linkage is not stored on the controller instance
(a previous bug used a shared dict attribute, causing cross-thread leaks).
"""

from __future__ import annotations

import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from moralstack.runtime.orchestrator import ProcessedRequest, create_minimal_orchestrator
from tests.test_orchestrator import MockPolicyLLM, MockRiskEstimator


class SlowMockRiskEstimator(MockRiskEstimator):
    """Adds a small sleep so threadpool workers interleave under load."""

    def estimate(self, prompt: str, **kwargs):  # type: ignore[override]
        time.sleep(random.uniform(0.02, 0.08))
        return super().estimate(prompt, **kwargs)


class GateMockRiskEstimator(MockRiskEstimator):
    """Blocks the first thread inside estimate until ``resume`` is set."""

    def __init__(self, entered: threading.Event, resume: threading.Event, *, default_score: float = 0.1) -> None:
        super().__init__(default_score=default_score)
        self._entered = entered
        self._resume = resume

    def estimate(self, prompt: str, **kwargs):  # type: ignore[override]
        if prompt.startswith("BLOCK:"):
            self._entered.set()
            if not self._resume.wait(timeout=20.0):
                raise AssertionError("GateMockRiskEstimator: resume not set")
        return super().estimate(prompt, **kwargs)


def test_concurrent_process_does_not_leak_conversation_id_across_threads() -> None:
    """N parallel process() calls each keep their own conversation_id on the result."""
    orch = create_minimal_orchestrator(
        policy=MockPolicyLLM(),
        risk_estimator=SlowMockRiskEstimator(),
    )
    n = 10
    lock = threading.Lock()
    results: dict[str, str | None] = {}
    errors: list[BaseException] = []

    def run_one(i: int) -> None:
        conv_id = f"conv-{i:03d}"
        try:
            req = ProcessedRequest(prompt=f"hello weather parallel {i}")
            out = orch.process(
                req,
                conversation_id=conv_id,
                turn_index=i,
                parent_request_id=req.request_id,
            )
            with lock:
                results[conv_id] = out.conversation_id
        except BaseException as e:
            with lock:
                errors.append(e)

    with ThreadPoolExecutor(max_workers=n) as ex:
        futures = [ex.submit(run_one, i) for i in range(n)]
        for f in as_completed(futures):
            f.result()

    assert not errors, f"Unexpected errors: {errors}"
    assert len(results) == n
    for expected, actual in results.items():
        assert actual == expected, f"Race: process(conversation_id={expected!r}) produced result.conversation_id={actual!r}"


def test_deterministic_interleave_conversation_id_on_shared_controller() -> None:
    """
    Thread A blocks in risk estimate; thread B completes a full process() in between.
    A's result must still show A's conversation_id (not B's).
    """
    entered = threading.Event()
    resume = threading.Event()
    orch = create_minimal_orchestrator(
        policy=MockPolicyLLM(),
        risk_estimator=GateMockRiskEstimator(entered, resume),
    )
    out_a: dict[str, object] = {}

    def thread_a() -> None:
        req = ProcessedRequest(prompt="BLOCK: hello weather")
        out_a["r"] = orch.process(
            req,
            conversation_id="conv-aaa",
            turn_index=0,
            parent_request_id=req.request_id,
        )

    t = threading.Thread(target=thread_a, name="thread-a")
    t.start()
    assert entered.wait(timeout=15.0), "thread A did not reach risk estimate"

    req_b = ProcessedRequest(prompt="hello weather")
    out_b = orch.process(
        req_b,
        conversation_id="conv-bbb",
        turn_index=0,
        parent_request_id=req_b.request_id,
    )
    resume.set()
    t.join(timeout=20.0)
    assert not t.is_alive()

    res_a = out_a["r"]
    assert getattr(res_a, "conversation_id", None) == "conv-aaa"
    assert out_b.conversation_id == "conv-bbb"

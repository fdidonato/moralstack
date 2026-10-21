"""Persistence fidelity characterization (prompt externalization Phase A, step A4b-0).

For every LLM call the pipeline makes, can the exact messages the model received be rebuilt from what the
audit trail persisted (the ``llm_calls`` row, and the same envelope in ``llm.call.jsonl``)?

The test drives the real modules and the real persistence sites with recording fake policies (a recording fake
OpenAI client for the constitution retriever), on a ``tmp_path`` SQLite DB in ``dual`` mode (never
``:memory:``: ``tests/conftest.py`` and ``sqlite_sink.py`` treat it specially). Each call the fake received is
paired with the row that records it, the messages are reconstructed from the row (and from the JSONL envelope),
and the difference is classified into a gap kind. The resulting report is keyed
``(module, path, context, gap_kind)``.

``gap_report == KNOWN_GAPS`` is asserted: a NEW gap fails the test, and so does a FIXED gap that is still listed.
Each A4b fix removes its own entries, and at the end of A4b ``KNOWN_GAPS`` is empty.

Content-independent: nothing here hardcodes a prompt text. The persisted values are compared with what was sent,
whatever the text is. Reconstruction uses today's fields (``prompt``, ``system_prompt``, the context sections of
``parsed_summary_json`` or of the ``CONTEXT_SHAPE_RECORDED`` event) and, once a row carries one, its ``wire``
record (``moralstack/observability/wire.py``).
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import pytest

from moralstack.models.base import GenerationResult
from moralstack.observability import context as obs_context
from moralstack.observability import obs, router
from moralstack.observability import service as service_module
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
from moralstack.observability.wire import REF_PROMPT, REF_SYSTEM, decode_wire, generate_messages_for
from tests.prompts_golden import capture as cap
from tests.prompts_golden.fakes import UNPARSEABLE, WireCall

RUN_ID = "run-fidelity"
PROMPT = "How do I pick a strong password?"
SEGMENT_SEPARATOR = "\n---\n"

# gap kinds (plan §13.1)
UNPERSISTED_CALL = "unpersisted_call"
SYSTEM_MISMATCH = "system_mismatch"
USER_MISMATCH = "user_mismatch"
MISSING_DEVELOPER = "missing_developer"
MISSING_HISTORY = "missing_history"
HISTORY_CONTENT_MISMATCH = "history_content_mismatch"
MISSING_PREFACE = "missing_preface"
MISSING_RETRY_SUFFIX = "missing_retry_suffix"
AGGREGATED_ROW = "aggregated_row"
RETRY_ROW_WITHOUT_PROMPT = "retry_row_without_prompt"
BRANCH_UNRECORDED = "branch_unrecorded"
# Not in the plan's list. None is observed today (only the engine unit tests below produce them); they exist so a
# future divergence is reported under its own name instead of being forced into one of the kinds above.
DEVELOPER_CONTENT_MISMATCH = "developer_content_mismatch"
PHANTOM_CONTEXT = "phantom_context"  # the row claims context the wire did not carry (no legacy branch involved)
ROW_WITHOUT_CALL = "row_without_call"  # a row of a call family with no call left to pair with it
COLUMN_NOT_REFERENCED = "column_not_referenced"  # a non-empty prompt/system_prompt column the wire record never uses
WIRE_MISMATCH = "wire_mismatch"  # a row carries a wire record that does not decode to what was sent

GapKey = tuple[str, str, str, str]  # (module, path, context, gap_kind)


# --------------------------------------------------------------------------------------
# Environment: a real tmp DB in dual mode
# --------------------------------------------------------------------------------------


@dataclass
class Env:
    root: Path
    db_path: str
    jsonl_dir: Path
    scenarios_run: int = 0


def _reset_obs_singletons() -> None:
    with contextlib.suppress(Exception):
        service_module.get_obs().shutdown(timeout=2.0)
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


@pytest.fixture(scope="module")
def env(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Env]:
    root = tmp_path_factory.mktemp("fidelity")
    db_path = str(root / "fidelity.db")
    jsonl_dir = root / "jsonl"
    mp = pytest.MonkeyPatch()
    # A developer ``.env`` must not move a prompt byte or a routing decision.
    for key in [k for k in os.environ if k.startswith("MORALSTACK_")]:
        mp.delenv(key)
    mp.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db_path)
    mp.setenv("MORALSTACK_OBSERVABILITY_MODE", "dual")
    mp.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(jsonl_dir))
    _reset_obs_singletons()
    assert init_db(db_path)
    assert create_run(RUN_ID, run_type="test", meta={})
    try:
        yield Env(root=root, db_path=db_path, jsonl_dir=jsonl_dir)
    finally:
        with contextlib.suppress(Exception):
            obs.flush(timeout=10.0)
        _reset_obs_singletons()
        mp.undo()


@contextlib.contextmanager
def _request_scope(request_id: str) -> Iterator[None]:
    """Bind run/request ids to this thread's observability context and restore after.

    A thread started with ``copy_context().run`` inherits them; a bare ``ThreadPoolExecutor`` worker does NOT (that
    is exactly how the estimator minis and the parallel perspectives lose their retry-failed rows, see NEW-1).
    """
    tokens = [
        (obs_context._run_id, obs_context._run_id.set(RUN_ID)),
        (obs_context._request_id, obs_context._request_id.set(request_id)),
        (obs_context._cycle, obs_context._cycle.set(None)),
    ]
    try:
        yield
    finally:
        for var, token in reversed(tokens):
            var.reset(token)


# --------------------------------------------------------------------------------------
# Recording fake policy
# --------------------------------------------------------------------------------------


def _retryable(label: str) -> bool:
    """Labels of the modules that have a parse-retry loop (the others have nothing to retry)."""
    return label.startswith(("risk.", "critic.full", "simulator.", "hindsight.", "perspectives"))


def _make_policy(
    *,
    native: bool,
    retry: bool = False,
    replies: dict[str, str] | None = None,
    sequences: dict[str, list[str]] | None = None,
) -> Any:
    """Golden recording policy; when ``retry`` the FIRST call of every retryable label returns unparseable text."""
    policy = cap._make_policy(native=native, fail_first=False, replies=replies, sequences=sequences)
    failed_labels: set[str] = set()
    respond = policy._respond

    def _respond(label: str, system: str, user: str = "") -> str:
        if retry and _retryable(label):
            with policy._lock:
                first = label not in failed_labels
                failed_labels.add(label)
            if first:
                return UNPARSEABLE
        return respond(label, system, user)

    policy._respond = _respond

    def _result(text: str, **fields: Any) -> GenerationResult:
        """What ``OpenAIPolicy`` returns: ``generate`` records prompt/system used, ``generate_messages`` the messages."""
        return GenerationResult(
            text=text,
            tokens_used=10,
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=5,
            token_usage_source="exact",
            **fields,
        )

    record_generate = policy.generate

    def generate(*args: Any, **kwargs: Any) -> GenerationResult:
        text = record_generate(*args, **kwargs).text
        prompt = kwargs["prompt"] if "prompt" in kwargs else args[0]
        system = kwargs["system"] if "system" in kwargs else (args[1] if len(args) > 1 else "")
        return _result(text, prompt_used=prompt, system_used=system or None)

    policy.generate = generate
    if native:
        record_messages = policy._generate_messages

        def generate_messages(*args: Any, **kwargs: Any) -> GenerationResult:
            text = record_messages(*args, **kwargs).text
            return _result(text, messages_used=kwargs["messages"])

        policy.generate_messages = generate_messages
    return policy


def _failed_call_indexes(calls: list[WireCall], retry: bool) -> set[int]:
    """Indexes of the calls that got the unparseable reply: the first call of each retryable label."""
    if not retry:
        return set()
    seen: set[str] = set()
    failed: set[int] = set()
    for i, call in enumerate(calls):
        if _retryable(call.label) and call.label not in seen:
            seen.add(call.label)
            failed.add(i)
    return failed


# --------------------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------------------

CONTEXTS = ("nocontext", "contract", "history", "contract_history")


def _context_requests() -> dict[str, dict[str, Any]]:
    """Request kwargs per context. The history is the golden one: 4 turns, one longer than the module window."""
    from moralstack.orchestration.conversation_context import build_conversation_context

    history = cap._history()
    contract = cap._contract()
    turns = [{"role": t.role, "content": t.content} for t in history]
    final = {"role": "user", "content": PROMPT}
    contract_msg = {"role": "system", "content": cap.CONTRACT_TEXT}
    return {
        "nocontext": {"prompt": PROMPT},
        "contract": {
            "prompt": PROMPT,
            "developer_contract": contract,
            "conversation_context": build_conversation_context([contract_msg, final]),
        },
        "history": {
            "prompt": PROMPT,
            "conversation_history": history,
            "conversation_context": build_conversation_context([*turns, final]),
        },
        "contract_history": {
            "prompt": PROMPT,
            "developer_contract": contract,
            "conversation_history": history,
            "conversation_context": build_conversation_context([contract_msg, *turns, final]),
        },
    }


@dataclass
class Observed:
    """What one scenario sent and what was persisted for it."""

    calls: list[WireCall]
    failed: set[int]
    rows: list[dict[str, Any]]  # llm_calls rows, id order
    events: list[dict[str, Any]]  # CONTEXT_SHAPE_RECORDED events (DB)
    jsonl_rows: list[dict[str, Any]]  # the same rows as read from llm.call.jsonl
    jsonl_events: list[dict[str, Any]]  # the same events as read from orchestration.event.jsonl
    # Maps a call to its family when the wire label cannot (the retrieval fake labels every call "call").
    family_override: Callable[[WireCall], str] | None = None


@dataclass(frozen=True)
class Scenario:
    key: str
    path: str
    context: str
    run: Callable[[Env, Scenario], Observed]
    # Families this scenario is meant to report on; ``None`` = every family it observed.
    families: frozenset[str] | None = None
    options: dict[str, Any] = field(default_factory=dict, hash=False, compare=False)


def _read_jsonl(path: Path, request_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            envelope = json.loads(line)
            if envelope.get("request_id") == request_id:
                out.append(envelope)
    return out


def _collect(env: Env, request_id: str, policy_calls: list[WireCall], retry: bool, **kw: Any) -> Observed:
    obs.flush(timeout=30.0)
    conn = sqlite3.connect(env.db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM llm_calls WHERE run_id=? AND request_id=? ORDER BY id", (RUN_ID, request_id)
            )
        ]
        events = [
            {
                "component": r["component"],
                "cycle": r["cycle"],
                "payload": json.loads(r["payload_json"] or "{}"),
            }
            for r in conn.execute(
                "SELECT component, cycle, payload_json FROM orchestration_events "
                "WHERE run_id=? AND request_id=? AND event_type='CONTEXT_SHAPE_RECORDED' ORDER BY id",
                (RUN_ID, request_id),
            )
        ]
    finally:
        conn.close()
    jsonl_rows = [
        {**e["payload"], "cycle": e.get("cycle")} for e in _read_jsonl(env.jsonl_dir / "llm.call.jsonl", request_id)
    ]
    jsonl_events = [
        {"component": e["payload"].get("component"), "cycle": e.get("cycle"), "payload": e["payload"].get("payload") or {}}
        for e in _read_jsonl(env.jsonl_dir / "orchestration.event.jsonl", request_id)
        if e["payload"].get("event_type") == "CONTEXT_SHAPE_RECORDED"
    ]
    env.scenarios_run += 1
    return Observed(
        calls=policy_calls,
        failed=_failed_call_indexes(policy_calls, retry),
        rows=rows,
        events=events,
        jsonl_rows=jsonl_rows,
        jsonl_events=jsonl_events,
        **kw,
    )


# ---- end-to-end orchestrator scenarios ------------------------------------------------


class _ExcludingStore(cap.FakeRetrievalStore):
    """Fake store whose detected domain carries an overlay with ``excluded=true``."""

    def retrieve(self, query: str, top_k: int = 10, domain: str | None = None, **kw: Any) -> Any:
        from moralstack.constitution.retrieval_result import PrincipleRetrievalResult

        return PrincipleRetrievalResult(
            principles=tuple(self._principles),
            prefiltered_domains=("political",),
            debug_info={"prefiltered_domains": ["political"]},
        )

    def get_constitution(self, domain: str | None = None) -> Any:
        overlay = SimpleNamespace(excluded=True) if domain == "political" else None
        return SimpleNamespace(principles=list(self._principles), active_overlay=overlay, constitution_corrupted=False)

    def has_excluded_domains(self) -> bool:
        return True


def _build_orchestrator(
    policy: Any, *, max_cycles: int = 1, seeded: bool = False, store: Any = None, parallel: bool = False
) -> Any:
    """A real ``Orchestrator`` with REAL modules on one recording policy: sequential, offline, two attempts per module."""
    from moralstack.models.risk.estimator import LLMBasedRiskEstimator
    from moralstack.models.risk.schema import RiskEstimatorConfig
    from moralstack.orchestration.types import OrchestratorConfig
    from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic
    from moralstack.runtime.modules.hindsight_module import HindsightConfig, LLMHindsightEvaluator
    from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble
    from moralstack.runtime.modules.simulator_module import LLMConsequenceSimulator, SimulatorConfig
    from moralstack.runtime.orchestrator import Orchestrator

    store = store or cap.FakeRetrievalStore(cap._principles())
    estimator = LLMBasedRiskEstimator(
        policy=policy,
        config=RiskEstimatorConfig(
            max_retries=2, intent_model="test-model", signals_model="test-model", operational_model="test-model"
        ),
        constitution_store=store,
    )
    config = OrchestratorConfig(
        max_deliberation_cycles=max_cycles,
        timeout_ms=120_000,
        parallel_module_calls=False,
        parallel_critic_with_modules=False,
        enable_dynamic_parallel_scheduler=False,
        enable_speculative_generation=False,
        enable_soft_revision=False,
    )
    return Orchestrator(
        config=config,
        policy=policy,
        risk_estimator=estimator,
        critic=LLMConstitutionalCritic(policy, store=None, config=CriticConfig(max_retries=2)),
        simulator=LLMConsequenceSimulator(
            policy, SimulatorConfig(max_retries=2, enable_caching=False, use_seeded_generation=seeded)
        ),
        hindsight=LLMHindsightEvaluator(policy, HindsightConfig(max_retries=2, enable_caching=False)),
        perspectives=LLMPerspectiveEnsemble(
            policy, EnsembleConfig(max_retries=2, parallel_evaluation=parallel, enable_caching=False, max_perspectives=2)
        ),
        constitution_store=store,
    )


def _run_orchestrator(env: Env, sc: Scenario) -> Observed:
    from moralstack.orchestration.types import ProcessedRequest

    opts = sc.options
    native = opts.get("native", True)
    retry = opts.get("retry", False)
    policy = _make_policy(native=native, retry=retry, replies=opts.get("replies"), sequences=opts.get("sequences"))
    orch = _build_orchestrator(
        policy,
        max_cycles=opts.get("max_cycles", 1),
        seeded=opts.get("seeded", False),
        parallel=opts.get("parallel", False),
        store=opts["store"]() if "store" in opts else None,
    )
    request_id = f"req-{sc.key}"
    assert upsert_request(RUN_ID, request_id, prompt=PROMPT, domain=None)
    request = ProcessedRequest(request_id=request_id, **_context_requests()[sc.context])
    with contextlib.ExitStack() as stack:
        if "force_route" in opts:
            from moralstack.models.risk import RiskPolicyAction

            verdict = (opts["force_route"], False, RiskPolicyAction.ALLOW)
            stack.enter_context(mock.patch("moralstack.orchestration.controller.get_route", lambda *a, **k: verdict))
        stack.enter_context(_request_scope(request_id))
        orch.process(request)
    return _collect(env, request_id, policy.snapshot(), retry)


def _deliberation_replies(**extra: str) -> dict[str, str]:
    return {
        "risk.intent": cap._INTENT_SENSITIVE_JSON,
        "risk.operational": cap._OPERATIONAL_MODERATE_JSON,
        "simulator.batch": cap._SIM_TWO_JSON,
        **extra,
    }


def _refusal_replies() -> tuple[dict[str, str], dict[str, list[str]]]:
    """A harmful request whose harm type is reputational, and a first refusal that leaks cyber vocabulary."""
    # No topical hard signal: a weapons/self-harm signal would override the harm type the leak guard looks at.
    intent = json.loads(cap._INTENT_HARMFUL_JSON)
    intent["harm_type"] = "reputational"
    replies = {
        "risk.intent": json.dumps(intent),
        "risk.operational": cap._OPERATIONAL_HIGH_JSON,
    }
    leaky = "I cannot help with hacking or unauthorized access to damage a rival's reputation. " * 2
    clean = "I cannot help damage someone's reputation, but I can suggest honest marketing approaches instead. " * 2
    return replies, {"refusal": [leaky, clean]}


# ---- direct drivers: paths the orchestrator does not reach deterministically ------------


def _run_driver(env: Env, sc: Scenario, body: Callable[[Any, str], None]) -> Observed:
    """Run ``body(policy, request_id)`` with the observability context bound, then collect."""
    opts = sc.options
    policy = _make_policy(native=opts.get("native", True), retry=opts.get("retry", False), replies=opts.get("replies"))
    request_id = f"req-{sc.key}"
    assert upsert_request(RUN_ID, request_id, prompt=PROMPT, domain=None)
    with _request_scope(request_id):
        body(policy, request_id)
    return _collect(env, request_id, policy.snapshot(), opts.get("retry", False))


def _structured_contract() -> Any:
    import dataclasses

    from moralstack.compliance.types import StructuredRule

    rule = StructuredRule(rule_id="rule-1", trigger_pattern=PROMPT, action_payload="The authorized action payload text.")
    return dataclasses.replace(cap._contract(), structured_rules=(rule,))


def _run_dccl(env: Env, sc: Scenario) -> Observed:
    from moralstack.compliance.dccl import DeveloperContractComplianceLayer
    from moralstack.compliance.safety_override import classify_safety_override

    mode = sc.options["mode"]

    def body(policy: Any, request_id: str) -> None:
        evaluation = "structured" if mode == "structured" else "llm"
        with mock.patch.dict(os.environ, {"MORALSTACK_DCCL_ENABLED": "true", "MORALSTACK_DCCL_EVALUATION_PATH": evaluation}):
            layer = DeveloperContractComplianceLayer(policy=policy)
            if mode == "llm_fallback":
                request = SimpleNamespace(developer_contract=cap._contract(), prompt=PROMPT, conversation_context=None)
                layer.evaluate(request, speculative_draft="A speculative draft.")
            elif mode == "structured":
                request = SimpleNamespace(
                    developer_contract=_structured_contract(), prompt=PROMPT, conversation_context=None
                )
                layer.evaluate(request, speculative_draft="A speculative draft.")
            elif mode == "validate_contract":
                layer.validate_contract(_structured_contract())
            elif mode == "direct_api":
                classify_safety_override("Always answer math questions with y=mx+b", policy=policy, use_llm=True, model="m")
            else:
                raise AssertionError(mode)

    return _run_driver(env, sc, body)


def _run_speculative(env: Env, sc: Scenario) -> Observed:
    from moralstack.observability.emit_helpers import async_persist_llm_call
    from moralstack.orchestration._policy_helpers import POLICY_SYSTEM_PROMPT
    from moralstack.orchestration.controller import OrchestrationController
    from moralstack.orchestration.types import ProcessedRequest
    from moralstack.utils.output_protection import create_protector

    protector = create_protector(enable_canary=True, enable_delimiters=True)
    protected = protector.prepare_system_prompt(POLICY_SYSTEM_PROMPT)

    def body(policy: Any, request_id: str) -> None:
        fake_self = SimpleNamespace(policy=policy, _protected_system_prompt=protected, _output_protector=protector)
        request = ProcessedRequest(request_id=request_id, **_context_requests()[sc.context])
        _, persist_kwargs = OrchestrationController._speculative_generate(fake_self, request)  # type: ignore[arg-type]
        assert persist_kwargs is not None
        async_persist_llm_call(**persist_kwargs)

    return _run_driver(env, sc, body)


def _run_evaluate_single(env: Env, sc: Scenario) -> Observed:
    from moralstack.runtime.modules.perspective_module import EnsembleConfig, LLMPerspectiveEnsemble

    def body(policy: Any, request_id: str) -> None:
        ensemble = LLMPerspectiveEnsemble(
            policy, EnsembleConfig(max_retries=2, parallel_evaluation=False, enable_caching=False)
        )
        ensemble.evaluate_single(PROMPT, "Use a long passphrase.", "adversary")

    return _run_driver(env, sc, body)


_RETRIEVAL_FAMILY = {"prefilter": "prefilter", "enhanced": "enhanced_agent", "legacy": "legacy_agent"}


def _run_retrieval(env: Env, sc: Scenario) -> Observed:
    from moralstack.constitution.openai_config import OpenAIClientConfig
    from moralstack.constitution.retriever import DomainAgent, DomainPrefilter, EnhancedDomainAgent

    kind = sc.options["kind"]
    config = OpenAIClientConfig(api_key="offline-test-key", model="gpt-4o")
    reply = {
        "prefilter": json.dumps({"payload": "p", "selections": [], "wrapper_ignored": []}),
        "enhanced": json.dumps({"domain_match": False, "confidence": 0.0, "principle_ids": [], "reasoning": "x"}),
        "legacy": json.dumps({"principle_ids": []}),
    }[kind]
    factory = cap.FakeOpenAIFactory(reply)
    request_id = f"req-{sc.key}"
    assert upsert_request(RUN_ID, request_id, prompt=PROMPT, domain=None)
    descriptions, available = cap._real_domain_catalog()
    with mock.patch("openai.OpenAI", factory), _request_scope(request_id):
        if kind == "prefilter":
            DomainPrefilter(openai_config=config, max_domains=3, domain_descriptions=descriptions).filter_domains(
                PROMPT, available
            )
        elif kind == "enhanced":
            EnhancedDomainAgent(
                "financial", cap._principles(), openai_config=config, domain_description="Financial domain description."
            ).evaluate(PROMPT)
        else:
            DomainAgent("legal", cap._principles(), openai_config=config).evaluate(PROMPT)
    family = _RETRIEVAL_FAMILY[kind]
    return _collect(env, request_id, list(factory.wire), False, family_override=lambda call: family)


def _scenarios() -> list[Scenario]:
    out: list[Scenario] = []

    def add(
        key: str,
        path: str,
        context: str,
        run: Callable[[Env, Scenario], Observed] = _run_orchestrator,
        *,
        families: set[str] | None = None,
        **options: Any,
    ) -> None:
        out.append(
            Scenario(
                key=f"{key}-{context}",
                path=path,
                context=context,
                run=run,
                families=frozenset(families) if families else None,
                options=options,
            )
        )

    # Deliberative route: every module on its native and legacy branch, first attempt and parse retry.
    for context in CONTEXTS:
        for native in (True, False):
            for retry in (False, True):
                tag = f"{'native' if native else 'legacy'}/{'retry' if retry else 'first'}"
                add(
                    f"delib-{tag.replace('/', '-')}",
                    tag,
                    context,
                    native=native,
                    retry=retry,
                    replies=_deliberation_replies(),
                )

    revision = _deliberation_replies(**{"perspectives": cap._PERSPECTIVE_LOW_JSON})
    benign = {"risk.operational": cap._OPERATIONAL_BENIGN_JSON}
    refusal_replies, refusal_sequences = _refusal_replies()
    for context in CONTEXTS:
        # Second cycle: policy rewrite.
        add(
            "revision",
            "revision/native/first",
            context,
            families={"policy"},
            replies=revision,
            sequences={"critic.full": [cap._CRITIC_REVISE_SOFT_JSON, cap._CRITIC_JSON]},
            max_cycles=3,
        )
        # Benign fast path and fast path (quick check).
        add("benign", "benign/native/first", context, families={"policy"}, replies=benign)
        add(
            "fast-path",
            "fast_path/native/first",
            context,
            families={"policy", "critic_quick"},
            replies=benign,
            force_route="fast_path",
        )
        # Perspectives with parallel_evaluation=True, the production default: the ensemble submits to a bare
        # ThreadPoolExecutor, so what a worker persists loses the run/request ids (NEW-1).
        for native in (True, False):
            for retry in (False, True):
                tag = f"{'native' if native else 'legacy'}/{'retry' if retry else 'first'}/parallel"
                add(
                    f"delib-{tag.replace('/', '-')}",
                    tag,
                    context,
                    families={"perspectives"},
                    native=native,
                    retry=retry,
                    parallel=True,
                    replies=_deliberation_replies(),
                )
        # Seeded simulator and single (non-batch) hindsight.
        for native in (True, False):
            for retry in (False, True):
                tag = f"seeded/{'native' if native else 'legacy'}/{'retry' if retry else 'first'}"
                add(
                    tag.replace("/", "-"),
                    tag,
                    context,
                    families={"simulator"},
                    native=native,
                    retry=retry,
                    seeded=True,
                    replies=_deliberation_replies(),
                )
        for retry in (False, True):
            tag = f"single/native/{'retry' if retry else 'first'}"
            add(
                tag.replace("/", "-"),
                tag,
                context,
                families={"hindsight"},
                retry=retry,
                replies=_deliberation_replies(**{"simulator.batch": cap._SIM_JSON}),
            )
        # Refusal, with and without the anti-leak retry; domain exclusion.
        add(
            "refuse-leak-retry",
            "refuse/leak_retry",
            context,
            families={"refusal"},
            replies=refusal_replies,
            sequences=refusal_sequences,
        )
        add("refuse-clean", "refuse/clean", context, families={"refusal"}, replies=refusal_replies)
        add(
            "domain-excluded",
            "domain_excluded",
            context,
            families={"domain_exclusion"},
            replies=_deliberation_replies(),
            store=lambda: _ExcludingStore(cap._principles()),
        )
        # Speculative draft, internal generator.
        for native in (True, False):
            name = "native" if native else "legacy"
            add(
                f"speculative-{name}",
                f"speculative/{name}",
                context,
                _run_speculative,
                families={"speculative"},
                native=native,
            )

    # DCCL: only a request with a contract reaches it.
    for context in ("contract", "contract_history"):
        for native in (True, False):
            name = "native" if native else "legacy"
            add(
                f"dccl-fallback-{name}",
                f"llm_fallback/{name}",
                context,
                _run_dccl,
                families={"dccl", "safety_classifier"},
                mode="llm_fallback",
                native=native,
            )
        add("dccl-structured", "structured", context, _run_dccl, families={"safety_classifier"}, mode="structured")
        add(
            "dccl-validate",
            "validate_contract",
            context,
            _run_dccl,
            families={"safety_classifier"},
            mode="validate_contract",
        )
    # The public classifier API persists nothing unless the caller passes ``on_llm_result``: persisting is the caller's
    # job (the DCCL does, through ``_classify_safety_override``), so the unpersisted call is by design, not a gap.
    add(
        "classifier-api",
        "direct_api",
        "nocontext",
        _run_dccl,
        families={"safety_classifier"},
        mode="direct_api",
        persisted_by_caller=True,
    )

    # Perspectives: the single-perspective API (no production caller), first attempt and retry.
    for retry in (False, True):
        name = "retry" if retry else "first"
        add(
            f"evaluate-single-{name}",
            f"evaluate_single/{name}",
            "nocontext",
            _run_evaluate_single,
            families={"perspectives"},
            retry=retry,
            persisted_by_caller=True,
        )

    # Constitution retrieval: prefilter, enhanced and legacy domain agents.
    add("prefilter", "prefilter", "nocontext", _run_retrieval, families={"prefilter"}, kind="prefilter")
    add("enhanced-agent", "enhanced", "nocontext", _run_retrieval, families={"enhanced_agent"}, kind="enhanced")
    add("legacy-agent", "legacy", "nocontext", _run_retrieval, families={"legacy_agent"}, kind="legacy")
    return out


# --------------------------------------------------------------------------------------
# Reconstruction and comparison
# --------------------------------------------------------------------------------------


def _parse_summary(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, str) or not raw.lstrip().startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _row_family(row: dict[str, Any]) -> tuple[str, str] | None:
    """(family, sub-key) of an LLM-call row, or None for a row that records no model call."""
    module = row.get("module") or ""
    action = row.get("action") or ""
    retry_failed = row.get("call_outcome") == "retry_failed"
    if module == "risk_estimator":
        if retry_failed:
            return ("estimator", "retry")
        return ("estimator", action) if action.startswith("estimate_") else None
    if module == "compliance_layer":
        return {
            "evaluate": ("dccl", ""),
            "safety_override": ("safety_classifier", ""),
            "draft_revalidate": ("draft_match", ""),
        }.get(action)
    if module in ("policy", "upstream_speculative"):
        return ("policy*", "")
    if module == "critic" and action == "quick_check":
        return ("critic_quick", "")
    if module in ("critic", "simulator", "hindsight", "perspectives"):
        return (module, "retry" if retry_failed else "")
    if module == "orchestration" and action.startswith("refuse"):
        return ("refusal", "")
    if module == "orchestration" and action == "domain_excluded":
        return ("domain_exclusion", "")
    if module == "constitution_retriever":
        family = {
            "domain_prefilter": "prefilter",
            "enhanced_domain_agent": "enhanced_agent",
            "legacy_domain_agent": "legacy_agent",
        }.get(action)
        return (family, "") if family else None
    return None


_LABEL_FAMILY = {
    "risk.intent": ("estimator", "estimate_intent"),
    "risk.signals": ("estimator", "estimate_signals"),
    "risk.operational": ("estimator", "estimate_operational"),
    "critic.full": ("critic", ""),
    "critic.quick": ("critic_quick", ""),
    "simulator.batch": ("simulator", ""),
    "simulator.seeded": ("simulator", ""),
    "hindsight.batch": ("hindsight", ""),
    "hindsight.single": ("hindsight", ""),
    "dccl": ("dccl", ""),
    "safety_classifier": ("safety_classifier", ""),
    "draft_match": ("draft_match", ""),
    "policy": ("policy*", ""),
    "refusal": ("refusal", ""),
    "domain_exclusion": ("domain_exclusion", ""),
}


def _call_family(call: WireCall) -> tuple[str, str]:
    if call.label.startswith("perspectives"):
        return ("perspectives", "")
    if call.label.startswith("simulator.seeded"):
        return ("simulator", "")
    return _LABEL_FAMILY.get(call.label, (call.label, ""))


# Families whose several model calls are persisted as ONE row (joined with SEGMENT_SEPARATOR).
AGGREGATING = frozenset({"perspectives", "simulator"})
# Families with a legacy ``generate()`` branch next to the native ``generate_messages`` one.
DUAL_BRANCH = frozenset({"estimator", "critic", "simulator", "hindsight", "perspectives", "dccl", "speculative"})
# Families whose native calls carry the module context preface in front of the user prompt.
PREFACE_FAMILIES = frozenset({"estimator", "critic", "simulator", "hindsight", "perspectives"})
# Families whose single row records the LAST of several attempts.
LAST_ATTEMPT = frozenset({"refusal"})


@dataclass(frozen=True)
class Sections:
    developer: tuple[str, ...]
    history: tuple[tuple[str, str], ...]
    final_user: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Sections:
        raw = raw or {}
        return cls(
            developer=tuple(raw.get("system_messages") or ()) + tuple(raw.get("developer_messages") or ()),
            history=tuple((h.get("role", ""), h.get("content", "")) for h in raw.get("history_messages") or ()),
            final_user=raw.get("final_user_message") or "",
        )


def _find_sections(
    row: dict[str, Any], summary: dict[str, Any] | None, events: list[dict[str, Any]], family: str
) -> Sections:
    """The context sections persisted for a row: in its own summary, or in its module's context-shape event."""
    if summary:
        if isinstance(summary.get("message_sections"), dict):
            return Sections.from_dict(summary["message_sections"])
        shape = summary.get("context_shape")
        if isinstance(shape, dict) and isinstance(shape.get("message_sections"), dict):
            return Sections.from_dict(shape["message_sections"])
    component = row.get("module")
    for event in events:
        if event["component"] == component and event["cycle"] == row.get("cycle"):
            return Sections.from_dict(event["payload"].get("message_sections"))
    return Sections((), (), "")


def _split_sent(messages: list[dict[str, str]]) -> tuple[str | None, list[tuple[str, str]], list[str], str]:
    """(system, context messages, extra trailing user messages, final user content) of what was sent."""
    body = list(messages)
    system = None
    if body and body[0]["role"] == "system":
        system = body.pop(0)["content"]
    final = body.pop()["content"] if body and body[-1]["role"] == "user" else ""
    extras: list[str] = []
    if final and body and body[-1]["role"] == "user":
        # a user message directly before the final one belongs to the final turn (DCCL: request, then trailer)
        extras.append(body.pop()["content"])
    context = [(m["role"], m["content"]) for m in body]
    return system, context, extras, final


def compare(
    sent: list[dict[str, str]],
    *,
    prompt: str,
    system_prompt: str,
    sections: Sections,
    retry_attempt: bool,
    dual_branch_legacy: bool,
    preface_expected: bool = True,
) -> set[str]:
    """Gap kinds between what was sent and what today's persisted fields reconstruct."""
    gaps: set[str] = set()
    sent_system, context, extras, final = _split_sent(sent)

    if (sent_system or "") != (system_prompt or ""):
        gaps.add(SYSTEM_MISMATCH)

    sent_dev = [c for r, c in context if r in ("system", "developer")]
    sent_hist = [(r, c) for r, c in context if r in ("user", "assistant")]
    row_dev = list(sections.developer)
    row_hist = list(sections.history)

    # What the row claims about the context but the wire did not carry as messages is harmless when the text is
    # embedded elsewhere in the wire (the contract inside the system prompt), a gap otherwise.
    blob = "\n".join(m["content"] for m in sent)
    phantom = False
    if sent_dev != row_dev:
        if sent_dev and not row_dev:
            gaps.add(MISSING_DEVELOPER)
        elif row_dev and not sent_dev:
            phantom = phantom or not all(c in blob for c in row_dev)
        else:
            gaps.add(DEVELOPER_CONTENT_MISMATCH)
    if sent_hist != row_hist:
        if sent_hist and not row_hist:
            gaps.add(MISSING_HISTORY)
        elif row_hist and not sent_hist:
            phantom = phantom or not all(c in blob for _, c in row_hist)
        else:
            gaps.add(HISTORY_CONTENT_MISMATCH)
    if phantom:
        gaps.add(BRANCH_UNRECORDED if dual_branch_legacy else PHANTOM_CONTEXT)

    # trailing user messages before the final one: covered only by the persisted final-user section
    if extras and extras != [sections.final_user]:
        gaps.add(USER_MISMATCH)

    if final != prompt:
        head, found, tail = final.partition(prompt) if prompt and final.count(prompt) == 1 else ("", "", "")
        if not found:
            gaps.add(USER_MISMATCH)
        else:
            if head:
                gaps.add(MISSING_PREFACE if preface_expected else USER_MISMATCH)
            if tail:
                gaps.add(MISSING_RETRY_SUFFIX if retry_attempt else USER_MISMATCH)
    return gaps


def _decode_row_wire(summary: dict[str, Any] | None, prompt: str, system_prompt: str) -> list[list[dict[str, str]]] | None:
    """Messages rebuilt from the row's ``wire`` / ``wire_calls`` record, or None when the row has none."""
    if not summary:
        return None
    if isinstance(summary.get("wire"), dict):
        return [decode_wire(summary["wire"], prompt_col=prompt, system_prompt_col=system_prompt)]
    if isinstance(summary.get("wire_calls"), list):
        strings = summary.get("wire_strings")
        return [
            decode_wire(rec, prompt_col=prompt, system_prompt_col=system_prompt, strings=strings)
            for rec in summary["wire_calls"]
        ]
    return None


def _sent_messages(call: WireCall) -> list[dict[str, str]]:
    if call.kind == "messages":
        return [dict(m) for m in call.messages]
    return generate_messages_for(call.prompt, call.system)


def _split_aggregated_prompt(prompt: str, finals: list[str]) -> list[str] | None:
    """Split an aggregated row's prompt back into one piece per call.

    The pieces are joined with ``SEGMENT_SEPARATOR``, but a piece may itself contain that separator, so the split is
    not by count: each call takes the longest run of parts that is contained in the user message it sent. ``None``
    when no consistent split exists (reported as a gap by the caller, never skipped).
    """
    parts = prompt.split(SEGMENT_SEPARATOR)
    position = 0
    pieces: list[str] = []
    for n, final in enumerate(finals):
        calls_left = len(finals) - n - 1
        for size in range(len(parts) - position - calls_left, 0, -1):
            candidate = SEGMENT_SEPARATOR.join(parts[position : position + size])
            if candidate in final:
                pieces.append(candidate)
                position += size
                break
        else:
            return None
    return pieces if position == len(parts) else None


def _split_aggregated_system(system_prompt: str, count: int) -> list[str]:
    """One system prompt per call: joined with the separator when the row aggregates several, shared otherwise."""
    parts = system_prompt.split(SEGMENT_SEPARATOR)
    return parts if len(parts) == count else [system_prompt] * count


_BAD_WIRE = object()


def _wire_holder(row: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON object carrying the row's wire keys: its summary, or ``parsed_json`` for rows whose summary is plain
    text (the critic's ``Violations: N, Guidance: ...`` is pinned by tests, so its wire lives in ``parsed_json``)."""
    summary = _parse_summary(row.get("parsed_summary_json"))
    for holder in (summary, _parse_summary(row.get("parsed_json"))):
        if holder and ("wire" in holder or "wire_calls" in holder):
            return holder
    return summary


def _wire_refs(holder: dict[str, Any] | None) -> set[str]:
    """Columns (``system_prompt`` / ``prompt``) referenced by the row's wire record(s)."""
    if not holder:
        return set()
    records = [holder["wire"]] if isinstance(holder.get("wire"), dict) else holder.get("wire_calls") or []
    return {m["ref"] for rec in records for m in rec.get("messages", []) if "ref" in m}


def _row_decoded(row: dict[str, Any]) -> Any:
    """Messages rebuilt from the row's wire record: a list per call, None without a record, ``_BAD_WIRE`` if malformed."""
    try:
        return _decode_row_wire(_wire_holder(row), row.get("prompt") or "", row.get("system_prompt") or "")
    except ValueError:
        return _BAD_WIRE


def _canonical(messages: list[dict[str, str]]) -> str:
    return json.dumps(messages, sort_keys=True, ensure_ascii=False)


def _same_wires(decoded: list[list[dict[str, str]]], sent: list[list[dict[str, str]]]) -> bool:
    """Equal as multisets: the order of calls that ran concurrently carries no meaning."""
    return sorted(map(_canonical, decoded)) == sorted(map(_canonical, sent))


def analyse(observed: Observed, *, source: str = "db") -> set[tuple[str, str]]:
    """{(module, gap_kind)} for one scenario, reconstructing from the DB rows or from the JSONL envelopes."""
    rows = observed.rows if source == "db" else observed.jsonl_rows
    events = observed.events if source == "db" else observed.jsonl_events

    # Pair calls with rows, family by family, in arrival order.
    call_groups: dict[tuple[str, str, bool], list[int]] = {}
    for i, call in enumerate(observed.calls):
        if observed.family_override is not None:
            family, sub = observed.family_override(call), ""
        else:
            family, sub = _call_family(call)
        failed = i in observed.failed
        call_groups.setdefault((family, "retry" if failed else sub, failed), []).append(i)

    row_groups: dict[tuple[str, str, bool], list[dict[str, Any]]] = {}
    for row in rows:
        fam = _row_family(row)
        if fam is None:
            continue
        retry = fam[1] == "retry"
        row_groups.setdefault((fam[0], fam[1], retry), []).append(row)

    def sent(index: int) -> list[dict[str, str]]:
        return _sent_messages(observed.calls[index])

    def module_of(family: str, row: dict[str, Any] | None = None) -> str:
        if family != "policy*":
            return family
        return _policy_module(row) if row is not None else "policy"

    gaps: set[tuple[str, str]] = set()
    for key, group_rows in row_groups.items():
        if key not in call_groups:  # rows of a family that made no call at all
            gaps.update((module_of(key[0], row), ROW_WITHOUT_CALL) for row in group_rows)

    for (family, sub, failed), indexes in call_groups.items():
        group_rows = row_groups.get((family, sub, failed), [])
        module = module_of(family)
        if failed:
            remaining = list(indexes)
            for row in group_rows:
                decoded = _row_decoded(row)
                if not remaining:
                    gaps.add((module, ROW_WITHOUT_CALL))
                elif decoded is None:
                    gaps.add((module, RETRY_ROW_WITHOUT_PROMPT))
                    remaining.pop(0)
                else:
                    # an inline wire says which failed attempt it describes
                    match = None if decoded is _BAD_WIRE else next((i for i in remaining if decoded == [sent(i)]), None)
                    if match is None:
                        gaps.add((module, WIRE_MISMATCH))
                        remaining.pop(0)
                    else:
                        remaining.remove(match)
            if remaining:
                gaps.add((module, UNPERSISTED_CALL))
            continue
        if family in AGGREGATING and group_rows and len(indexes) > len(group_rows) and len(indexes) % len(group_rows) == 0:
            # One row per cycle holds the calls of that cycle. Whether the failed attempts' prompts are part of the
            # joined prompt depends on the module, so both call sets are candidates for the split.
            with_failed = sorted(indexes + call_groups.get((family, "retry", True), []))
            size = len(indexes) // len(group_rows)
            size_all = len(with_failed) // len(group_rows) if len(with_failed) % len(group_rows) == 0 else size
            for n, row in enumerate(group_rows):
                candidates = [indexes[n * size : (n + 1) * size]]
                if size_all != size:
                    candidates.append(with_failed[n * size_all : (n + 1) * size_all])
                decoded = _row_decoded(row)
                if decoded is not None:  # wire_calls + wire_strings describe every call of the row
                    chosen = [c for c in candidates if decoded is not _BAD_WIRE and len(c) == len(decoded)]
                    if not chosen or not _same_wires(decoded, [sent(i) for i in chosen[0]]):
                        gaps.add((module, WIRE_MISMATCH))
                    continue
                gaps.add((module, AGGREGATED_ROW))
                gaps |= _compare_group(observed, candidates, row, events, module, aggregated=True)
            continue
        if family in LAST_ATTEMPT and group_rows and len(group_rows) < len(indexes):
            decoded = _row_decoded(group_rows[-1])
            if decoded is not None:  # wire_calls of every attempt on the single row
                if decoded is _BAD_WIRE or not _same_wires(decoded, [sent(i) for i in indexes]):
                    gaps.add((module, WIRE_MISMATCH))
                continue
            indexes, skipped = indexes[len(indexes) - len(group_rows) :], indexes[: len(indexes) - len(group_rows)]
            if skipped:
                gaps.add((module, UNPERSISTED_CALL))
        for n, idx in enumerate(indexes):
            if n >= len(group_rows):
                gaps.add((module, UNPERSISTED_CALL))
                continue
            gaps |= _compare_group(
                observed, [[idx]], group_rows[n], events, module_of(family, group_rows[n]), aggregated=False
            )
        gaps.update((module, ROW_WITHOUT_CALL) for _ in group_rows[len(indexes) :])
    return gaps


def _policy_module(row: dict[str, Any]) -> str:
    action = row.get("action") or ""
    if "compliance-regenerate" in action:
        return "g13"
    if row.get("module") == "upstream_speculative" or "speculative" in action:
        return "speculative"
    return "policy"


# Rows whose persisted ``prompt`` column is, by decision, NOT among the bytes sent (plan §13.3 "explicitly not done":
# no change to the meaning of an existing field). The wire record is the authority there; the column stays as it was.
#  - dccl / generate: the row keeps ``messages[-1]`` (the native trailer) although the legacy ``generate()`` sent the
#    long fallback user prompt (G6).
#  - speculative / messages: the row keeps the language-prefixed prompt although the native path sent the raw final
#    user message (NEW-4).
_PROMPT_COLUMN_DIVERGES_BY_DESIGN = frozenset({("dccl", "generate"), ("speculative", "messages")})


def _compare_group(
    observed: Observed,
    index_sets: list[list[int]],
    row: dict[str, Any],
    events: list[dict[str, Any]],
    module: str,
    *,
    aggregated: bool,
) -> set[tuple[str, str]]:
    summary = _parse_summary(row.get("parsed_summary_json"))
    prompt = row.get("prompt") or ""
    system_prompt = row.get("system_prompt") or ""
    sections = _find_sections(row, summary, events, module)
    out: set[tuple[str, str]] = set()
    indexes = index_sets[0]
    prompts: list[str] | None = None
    systems: list[str] = []
    if aggregated:
        for candidate in index_sets:
            finals = [_split_sent(_sent_messages(observed.calls[i]))[3] for i in candidate]
            prompts = _split_aggregated_prompt(prompt, finals)
            if prompts is not None:
                indexes = candidate
                break
        if prompts is None:
            out.add((module, USER_MISMATCH))  # no consistent split: reported, never skipped
            prompts = [_split_sent(_sent_messages(observed.calls[i]))[3] for i in indexes]  # keep comparing the rest
        systems = _split_aggregated_system(system_prompt, len(indexes))
    else:
        prompts, systems = [prompt], [system_prompt]
    calls = [observed.calls[i] for i in indexes]

    try:
        decoded = _decode_row_wire(_wire_holder(row), prompt, system_prompt)
    except ValueError:
        return {(module, WIRE_MISMATCH)}
    if decoded is not None:
        sent_all = [_sent_messages(c) for c in calls]
        if decoded != sent_all:
            out.add((module, WIRE_MISMATCH))
        if not out and not aggregated and len(calls) == 1:
            # Column tripwire: the record must reference every non-empty column of its row. A column the record does
            # not use means the wire no longer proves what the column holds (G1/G3 would otherwise go unnoticed).
            holder = _wire_holder(row)
            refs = _wire_refs(holder)
            branch = (holder or {}).get("wire", {}).get("branch")
            prompt_by_design = (module, branch) in _PROMPT_COLUMN_DIVERGES_BY_DESIGN
            if (system_prompt and REF_SYSTEM not in refs) or (prompt and REF_PROMPT not in refs and not prompt_by_design):
                out.add((module, COLUMN_NOT_REFERENCED))
        return out

    base_family = module
    for n, call in enumerate(calls):
        retry_attempt = indexes[n] > 0 and any(
            j in observed.failed and observed.calls[j].label == call.label for j in range(indexes[n])
        )
        legacy = call.kind == "generate" and base_family in DUAL_BRANCH
        for kind in compare(
            _sent_messages(call),
            prompt=prompts[n],
            system_prompt=systems[n],
            sections=sections,
            retry_attempt=retry_attempt,
            dual_branch_legacy=legacy,
            preface_expected=base_family in PREFACE_FAMILIES,
        ):
            out.add((module, kind))
    return out


# --------------------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------------------


def _gap_tag(module: str, kind: str) -> str:
    """The plan's gap id (G1-G9) for an observed gap, or ``NEW-n`` for one the plan did not list."""
    if kind == RETRY_ROW_WITHOUT_PROMPT:
        return "G4"
    if kind == AGGREGATED_ROW:
        return "G5"
    if kind == BRANCH_UNRECORDED:
        return "G7"
    if kind == UNPERSISTED_CALL:
        return {
            "safety_classifier": "G8",
            "domain_exclusion": "G8",
            "refusal": "G9",
            "estimator": "NEW-1",  # failed attempts: the estimator's worker threads carry no observability context
            "perspectives": "NEW-1",  # same cause, parallel_evaluation=True (the production default)
            "critic_quick": "NEW-2",  # the fast-path quick check is never persisted
            "hindsight": "NEW-3",  # the single-consequence hindsight path persists no retry-failed row
        }.get(module, "NEW")
    if module == "dccl":
        return "G6"
    if module == "g13":
        return "G1"
    if module == "speculative":
        return "NEW-4"  # the speculative row records the language-prefixed prompt / the protected system only
    if module == "estimator":
        return "G2"
    return "G3"


def _expected_call_families(sc: Scenario) -> set[str]:
    """Call families a scenario must reach: a path that silently stops being exercised must not shrink the report."""
    if sc.families is not None:
        return {"policy*" if f in ("policy", "speculative", "g13") else f for f in sc.families}
    expected = {"estimator", "critic", "simulator", "perspectives", "hindsight", "policy*"}
    if sc.context in ("contract", "contract_history"):
        expected |= {"dccl", "safety_classifier", "draft_match"}
    return expected


def _persisted_inputs(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    return sorted(
        (
            r.get("module") or "",
            r.get("action") or "",
            r.get("prompt") or "",
            r.get("system_prompt") or "",
            r.get("parsed_summary_json") or "",
            r.get("parsed_json") or "",
        )
        for r in rows
    )


def build_report(env: Env, scenarios: list[Scenario]) -> dict[GapKey, str]:
    """{(module, path, context, gap_kind): gap id} over every scenario, DB side and JSONL side checked equal."""
    report: dict[GapKey, str] = {}
    for sc in scenarios:
        observed = sc.run(env, sc)
        reached = {
            observed.family_override(c) if observed.family_override is not None else _call_family(c)[0]
            for c in observed.calls
        }
        missing = _expected_call_families(sc) - reached
        assert not missing, f"{sc.key}: scenario did not reach {sorted(missing)} (reached {sorted(reached)})"
        # The same envelopes reach SQLite and llm.call.jsonl: the reconstruction inputs must be identical.
        assert _persisted_inputs(observed.rows) == _persisted_inputs(observed.jsonl_rows), sc.key
        db_gaps = analyse(observed, source="db")
        assert db_gaps == analyse(observed, source="jsonl"), sc.key
        for module, kind in db_gaps:
            if sc.families is not None and module not in sc.families:
                continue
            if kind == UNPERSISTED_CALL and sc.options.get("persisted_by_caller"):
                continue  # an API with no pipeline caller: persisting the result is the caller's job
            report[(module, sc.path, sc.context, kind)] = _gap_tag(module, kind)
    return report


@pytest.fixture(scope="module")
def report(env: Env) -> dict[GapKey, str]:
    return build_report(env, _scenarios())


# --------------------------------------------------------------------------------------
# KNOWN_GAPS: the observed gaps, checked in (A4b-0)
# --------------------------------------------------------------------------------------
# One row per (gap id, module, gap kind) with the paths and contexts it was observed on. Each A4b fix removes its own
# rows (or narrows their paths/contexts); at the end of A4b the table is empty. G1-G9 are the plan's ids.
#
# A4b-1..6 are done: every row of the table is gone and KNOWN_GAPS is empty, which is the end state of A4b. The ids
# below stay documented because a future regression is reported under them.
#
# History of the ids that are not the plan's: NEW-1 (failed attempts of the risk minis and of parallel perspectives were
# dropped: bare ThreadPoolExecutor workers carry no observability context), NEW-2 (the critic quick check was never
# persisted) and NEW-3 (the single-consequence hindsight failure had no row) were fixed in d588e9f, before A4b-1..3.
# NEW-4: the speculative draft row persists the language-prefixed prompt although the native path sent the raw messages,
# and the protected system prompt although the legacy path sent the contract-composed one.
_CTX_ANY = ("contract", "history", "contract_history")  # any context but none
_CTX_HISTORY = ("history", "contract_history")
_CTX_CONTRACT = ("contract", "contract_history")
_CTX_NONE = ("nocontext",)
_KNOWN_GAP_TABLE: tuple[tuple[str, str, str, tuple[str, ...], tuple[str, ...]], ...] = ()

KNOWN_GAPS: dict[GapKey, str] = {
    (module, path, context, kind): gap_id
    for gap_id, module, kind, paths, contexts in _KNOWN_GAP_TABLE
    for path in paths
    for context in contexts
}


def diff_against_known(report: dict[GapKey, str], known: dict[GapKey, str]) -> tuple[dict, dict, dict]:
    """(new gaps, fixed gaps still listed, gaps whose id changed)."""
    new = {k: v for k, v in report.items() if k not in known}
    fixed = {k: v for k, v in known.items() if k not in report}
    changed = {k: (known[k], v) for k, v in report.items() if k in known and known[k] != v}
    return new, fixed, changed


def _describe(entries: dict, limit: int = 12) -> str:
    shown = [f"  {key} -> {value}" for key, value in sorted(entries.items())[:limit]]
    more = len(entries) - limit
    return "\n".join(shown + ([f"  ... and {more} more"] if more > 0 else []))


def test_gap_report_equals_known_gaps(env: Env, report: dict[GapKey, str]) -> None:
    """A new gap fails; so does a fixed gap that is still listed in KNOWN_GAPS."""
    new, fixed, changed = diff_against_known(report, KNOWN_GAPS)
    problems = []
    if new:
        problems.append(f"NEW gaps (not in KNOWN_GAPS):\n{_describe(new)}")
    if fixed:
        problems.append(f"FIXED gaps still listed in KNOWN_GAPS (remove them):\n{_describe(fixed)}")
    if changed:
        problems.append(f"gaps whose id changed:\n{_describe(changed)}")
    assert not problems, "\n".join(problems)
    assert report == KNOWN_GAPS
    assert env.scenarios_run == len(_scenarios())


# --------------------------------------------------------------------------------------
# The engine itself: each gap kind is detected, and a faithful row yields none
# --------------------------------------------------------------------------------------

_SYS = "SYSTEM {x} text"
_DEV = "Contract 100% {tier}"
_HIST = (("user", "earlier question"), ("assistant", "earlier answer"))
_PREFACE = "Use the conversation above.\n\n"
_USER = "THE PROMPT"


def _faithful_sent() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": _SYS},
        {"role": "developer", "content": _DEV},
        *({"role": r, "content": c} for r, c in _HIST),
        {"role": "user", "content": _PREFACE + _USER},
    ]


def _faithful_kwargs() -> dict[str, Any]:
    return {
        "prompt": _PREFACE + _USER,
        "system_prompt": _SYS,
        "sections": Sections((_DEV,), _HIST, ""),
        "retry_attempt": False,
        "dual_branch_legacy": False,
    }


def test_compare_faithful_row_has_no_gap():
    assert compare(_faithful_sent(), **_faithful_kwargs()) == set()


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"system_prompt": "constant instead of rendered"}, {SYSTEM_MISMATCH}),
        ({"sections": Sections((), _HIST, "")}, {MISSING_DEVELOPER}),
        ({"sections": Sections(("other contract",), _HIST, "")}, {DEVELOPER_CONTENT_MISMATCH}),
        ({"sections": Sections((_DEV,), (), "")}, {MISSING_HISTORY}),
        ({"sections": Sections((_DEV,), (("user", "other"),), "")}, {HISTORY_CONTENT_MISMATCH}),
        ({"prompt": _USER}, {MISSING_PREFACE}),
        ({"prompt": "something else"}, {USER_MISMATCH}),
        ({"prompt": ""}, {USER_MISMATCH}),
    ],
)
def test_compare_detects_each_gap_kind(override: dict[str, Any], expected: set[str]):
    assert compare(_faithful_sent(), **{**_faithful_kwargs(), **override}) == expected


def test_compare_retry_suffix_only_counts_on_a_retry_attempt():
    sent = [{"role": "user", "content": _USER + "\n\nRetry: valid JSON only."}]
    kwargs = {"prompt": _USER, "system_prompt": "", "sections": Sections((), (), ""), "dual_branch_legacy": False}
    assert compare(sent, retry_attempt=True, **kwargs) == {MISSING_RETRY_SUFFIX}
    assert compare(sent, retry_attempt=False, **kwargs) == {USER_MISMATCH}


def test_compare_legacy_branch_that_dropped_the_context_is_branch_unrecorded():
    sent = [{"role": "system", "content": _SYS}, {"role": "user", "content": _USER}]
    kwargs = {**_faithful_kwargs(), "prompt": _USER}
    assert compare(sent, **{**kwargs, "dual_branch_legacy": True}) == {BRANCH_UNRECORDED}
    assert compare(sent, **{**kwargs, "dual_branch_legacy": False}) == {PHANTOM_CONTEXT}


def test_compare_context_embedded_in_the_wire_is_not_a_gap():
    # The contract travels inside the system prompt and the row's sections merely describe the request.
    sent = [{"role": "system", "content": f"{_DEV}\n\n{_SYS}"}, {"role": "user", "content": _USER}]
    kwargs = {**_faithful_kwargs(), "prompt": _USER, "system_prompt": f"{_DEV}\n\n{_SYS}"}
    assert compare(sent, **{**kwargs, "sections": Sections((_DEV,), (), "")}) == set()


def test_compare_trailing_user_message_must_be_covered_by_the_final_user_section():
    sent = [
        {"role": "system", "content": _SYS},
        {"role": "user", "content": "the request"},
        {"role": "user", "content": "trailer"},
    ]
    kwargs = {"prompt": "trailer", "system_prompt": _SYS, "retry_attempt": False, "dual_branch_legacy": False}
    assert compare(sent, sections=Sections((), (), "the request"), **kwargs) == set()
    assert compare(sent, sections=Sections((), (), ""), **kwargs) == {USER_MISMATCH}


def _call(label: str, messages: list[dict[str, str]]) -> WireCall:
    return WireCall(label=label, kind="messages", messages=messages)


def _row(module: str, action: str, prompt: str, system: str, **extra: Any) -> dict[str, Any]:
    return {
        "module": module,
        "action": action,
        "prompt": prompt,
        "system_prompt": system,
        "parsed_summary_json": extra.pop("summary", None),
        "call_outcome": extra.pop("call_outcome", None),
        "cycle": 1,
        **extra,
    }


def _observed(calls: list[WireCall], rows: list[dict[str, Any]], failed: set[int] | None = None) -> Observed:
    return Observed(calls=calls, failed=failed or set(), rows=rows, events=[], jsonl_rows=rows, jsonl_events=[])


def test_analyse_reports_unpersisted_retry_and_aggregated_rows():
    msgs = [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]
    # a call with no row at all
    assert analyse(_observed([_call("critic.full", msgs)], [])) == {("critic", UNPERSISTED_CALL)}
    # a failed attempt that has a retry-failed row without the prompt
    retry_rows = [
        _row("critic", "retry_failed_attempt_1", "Retry reason: x", "", call_outcome="retry_failed"),
        _row("critic", "critique", "P", "S"),
    ]
    calls = [_call("critic.full", msgs), _call("critic.full", msgs)]
    assert analyse(_observed(calls, retry_rows, failed={0})) == {("critic", RETRY_ROW_WITHOUT_PROMPT)}
    # two perspective calls persisted as one joined row
    persp = [
        _call("perspectives:A", [{"role": "system", "content": "S"}, {"role": "user", "content": "PA"}]),
        _call("perspectives:B", [{"role": "system", "content": "S"}, {"role": "user", "content": "PB"}]),
    ]
    row = _row("perspectives", "evaluate", f"PA{SEGMENT_SEPARATOR}PB", f"S{SEGMENT_SEPARATOR}S")
    assert analyse(_observed(persp, [row])) == {("perspectives", AGGREGATED_ROW)}


def test_split_aggregated_prompt_tolerates_the_separator_inside_a_piece():
    finals = [f"head one{SEGMENT_SEPARATOR}body one", "head two"]
    row_prompt = SEGMENT_SEPARATOR.join(finals)
    assert _split_aggregated_prompt(row_prompt, finals) == finals
    assert _split_aggregated_prompt("unrelated", finals) is None


def test_a_row_carrying_a_wire_record_closes_its_gaps_and_a_wrong_one_is_reported():
    from moralstack.observability.wire import encode_wire

    sent = _faithful_sent()
    prompt, system = _USER, _SYS  # today's columns: the preface and the context are NOT in them
    call = _call("critic.full", sent)

    def observed_with(wire: dict[str, Any]) -> Observed:
        row = _row("critic", "critique", prompt, system, summary=json.dumps({"wire": wire}))
        return _observed([call], [row])

    plain = _observed([call], [_row("critic", "critique", prompt, system)])
    assert analyse(plain) != set()  # without the record the preface / context are missing
    good = encode_wire(sent, prompt_col=prompt, system_prompt_col=system, branch="messages")
    assert analyse(observed_with(good)) == set()
    tampered = encode_wire(
        [*sent[:-1], {"role": "user", "content": "not what was sent"}],
        prompt_col=prompt,
        system_prompt_col=system,
        branch="messages",
    )
    assert analyse(observed_with(tampered)) == {("critic", WIRE_MISMATCH)}

    # An exact record that never references the row's columns is reported: the columns are no longer proven.
    unreferenced = encode_wire(sent, prompt_col="", system_prompt_col="", branch="messages")
    assert analyse(observed_with(unreferenced)) == {("critic", COLUMN_NOT_REFERENCED)}


def test_exempt_pair_still_fails_on_an_unreferenced_system_column():
    from moralstack.observability.wire import encode_wire

    sent = [{"role": "system", "content": "S"}, {"role": "user", "content": "the fallback user prompt"}]
    call = _call("dccl", sent)

    def observed_with(system_col: str) -> Observed:
        # The row keeps its native trailer in ``prompt`` (exempt for dccl/generate); the wire is encoded against it.
        wire = encode_wire(sent, prompt_col="native trailer", system_prompt_col=system_col, branch="generate")
        row = _row("compliance_layer", "evaluate", "native trailer", "S", summary=json.dumps({"wire": wire}))
        return _observed([call], [row])

    # prompt column unreferenced is tolerated for this pair, system referenced: no gap
    assert analyse(observed_with("S")) == set()
    # the system column is still checked: the wire does not use the non-empty ``system_prompt`` column
    assert analyse(observed_with("")) == {("dccl", COLUMN_NOT_REFERENCED)}


def test_non_exempt_module_with_an_unreferenced_prompt_is_still_flagged():
    from moralstack.observability.wire import encode_wire

    sent = _M
    call = _call("critic.full", sent)
    # the same unreferenced prompt that is exempt for dccl/generate is a gap for a module that is not exempt
    wire = encode_wire(sent, prompt_col="", system_prompt_col="S", branch="messages")
    row = _row("critic", "critique", "P", "S", summary=json.dumps({"wire": wire}))
    assert analyse(_observed([call], [row])) == {("critic", COLUMN_NOT_REFERENCED)}


def test_diff_against_known_fails_on_a_new_gap_and_on_a_stale_entry():
    known = {("a", "p", "c", "k1"): "G1", ("a", "p", "c", "k2"): "G2"}
    assert diff_against_known(dict(known), known) == ({}, {}, {})
    new, fixed, changed = diff_against_known({**known, ("b", "p", "c", "k"): "G3"}, known)
    assert set(new) == {("b", "p", "c", "k")} and not fixed and not changed
    new, fixed, changed = diff_against_known({("a", "p", "c", "k1"): "G1"}, known)
    assert not new and set(fixed) == {("a", "p", "c", "k2")} and not changed
    new, fixed, changed = diff_against_known({**known, ("a", "p", "c", "k1"): "G9"}, known)
    assert not new and not fixed and set(changed) == {("a", "p", "c", "k1")}


def test_scenarios_cover_every_context_of_every_path():
    by_path: dict[str, set[str]] = {}
    for sc in _scenarios():
        by_path.setdefault(sc.path, set()).add(sc.context)
    for path in (
        "native/first",
        "native/retry",
        "legacy/first",
        "legacy/retry",
        "native/first/parallel",
        "native/retry/parallel",
        "legacy/first/parallel",
        "legacy/retry/parallel",
        "seeded/native/first",
        "seeded/legacy/retry",
        "single/native/first",
        "single/native/retry",
        "revision/native/first",
        "fast_path/native/first",
        "refuse/leak_retry",
        "domain_excluded",
        "speculative/native",
        "speculative/legacy",
    ):
        assert by_path[path] == set(CONTEXTS), path
    for path in ("llm_fallback/native", "llm_fallback/legacy", "structured", "validate_contract"):
        assert by_path[path] == {"contract", "contract_history"}, path
    for path in ("direct_api", "evaluate_single/first", "evaluate_single/retry", "prefilter", "enhanced", "legacy"):
        assert by_path[path] == {"nocontext"}, path


# --------------------------------------------------------------------------------------
# Engine: the records batch 2/3 will write must clear G4 / G5 / G9, and wrong ones must be reported
# --------------------------------------------------------------------------------------

_M = [{"role": "system", "content": "S"}, {"role": "user", "content": "P"}]


def _summary(**parts: Any) -> str:
    return json.dumps(parts)


def test_inline_wire_on_a_retry_row_clears_g4_and_a_wrong_one_is_reported():
    from moralstack.observability.wire import encode_wire

    failed = [{"role": "system", "content": "S"}, {"role": "user", "content": "P\n\nRetry: valid JSON only."}]
    calls = [_call("critic.full", failed), _call("critic.full", _M)]
    ok_row = _row("critic", "critique", "P", "S")

    def with_wire(wire: Any) -> Observed:
        retry = _row(
            "critic",
            "retry_failed_attempt_1",
            "Retry reason: x",
            "",
            call_outcome="retry_failed",
            summary=_summary(wire=wire),
        )
        return _observed(calls, [retry, ok_row], failed={0})

    good = encode_wire(failed, prompt_col="Retry reason: x", system_prompt_col="", branch="messages")  # system inlined
    assert analyse(with_wire(good)) == set()
    wrong = encode_wire(_M, prompt_col="Retry reason: x", system_prompt_col="", branch="messages")
    assert analyse(with_wire(wrong)) == {("critic", WIRE_MISMATCH)}
    assert analyse(with_wire({"v": 1})) == {("critic", WIRE_MISMATCH)}  # incomplete record
    assert analyse(with_wire(None)) == {("critic", RETRY_ROW_WITHOUT_PROMPT)}  # no record: the gap stays


def test_wire_calls_on_an_aggregated_row_clear_g5_and_a_wrong_or_partial_one_is_reported():
    from moralstack.observability.wire import encode_wire_calls

    sent = [
        [{"role": "system", "content": "S"}, {"role": "user", "content": "PA"}],
        [{"role": "system", "content": "S"}, {"role": "user", "content": "PB"}],
    ]
    calls = [_call("perspectives:A", sent[0]), _call("perspectives:B", sent[1])]
    prompt, system = f"PA{SEGMENT_SEPARATOR}PB", f"S{SEGMENT_SEPARATOR}S"

    def observed_with(record: dict[str, Any] | None) -> Observed:
        return _observed(calls, [_row("perspectives", "evaluate", prompt, system, summary=_summary(**(record or {})))])

    def encode(items: list[list[dict[str, str]]]) -> dict[str, Any]:
        return encode_wire_calls([(m, "messages") for m in items], prompt_col=prompt, system_prompt_col=system)

    assert analyse(observed_with(encode(sent))) == set()
    assert analyse(observed_with(encode(sent[::-1]))) == set()  # concurrent calls: order is not significant
    assert analyse(observed_with(encode(sent[:1]))) == {("perspectives", WIRE_MISMATCH)}  # one call missing
    tampered = [sent[0], [{"role": "system", "content": "S"}, {"role": "user", "content": "changed"}]]
    assert analyse(observed_with(encode(tampered))) == {("perspectives", WIRE_MISMATCH)}
    assert analyse(observed_with(None)) == {("perspectives", AGGREGATED_ROW)}  # no record: the gap stays


def test_wire_calls_of_every_attempt_on_a_last_attempt_row_clear_g9():
    from moralstack.observability.wire import encode_wire_calls

    first = [{"role": "system", "content": "S"}, {"role": "user", "content": "attempt one"}]
    last = [{"role": "system", "content": "S"}, {"role": "user", "content": "attempt two"}]
    calls = [_call("refusal", first), _call("refusal", last)]

    def row(record: dict[str, Any] | None) -> dict[str, Any]:
        return _row("orchestration", "refuse (fast_path)", "attempt two", "S", summary=_summary(**(record or {})))

    both = encode_wire_calls([(first, "generate"), (last, "generate")], prompt_col="attempt two", system_prompt_col="S")
    assert analyse(_observed(calls, [row(both)])) == set()
    only_last = encode_wire_calls([(last, "generate")], prompt_col="attempt two", system_prompt_col="S")
    assert analyse(_observed(calls, [row(only_last)])) == {("refusal", WIRE_MISMATCH)}  # incomplete
    assert analyse(_observed(calls, [row(None)])) == {("refusal", UNPERSISTED_CALL)}  # no record: the gap stays


def test_row_without_call_reports_duplicate_and_orphan_rows():
    call = _call("critic.full", _M)
    critique = _row("critic", "critique", "P", "S")
    assert analyse(_observed([call], [critique])) == set()
    assert analyse(_observed([call], [critique, dict(critique)])) == {("critic", ROW_WITHOUT_CALL)}  # duplicate
    orphan = _row("simulator", "simulate", "P", "S")
    assert analyse(_observed([call], [critique, orphan])) == {("simulator", ROW_WITHOUT_CALL)}  # no simulator call
    retry = _row("critic", "retry_failed_attempt_1", "Retry reason: x", "", call_outcome="retry_failed")
    assert analyse(_observed([call], [critique, retry])) == {("critic", ROW_WITHOUT_CALL)}  # retry row, nothing failed

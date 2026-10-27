"""A2b: the deliberative modules (critic, simulator, hindsight, perspectives), ``message_context`` and the cache
tag read their prompts from the ``PromptSet`` they were given (plan sections 8 and 11, tests T4).

Tier 1 (content independent): marker sets prove which fragment reached the wire; assertions compare against the
live set and never against prompt literals. The only hard-coded values are the md5 cache keys captured from the
code BEFORE ``utils/cache.py`` was edited (``test_default_cache_keys_byte_identical``).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from moralstack.constitution.schema import Constitution, Principle
from moralstack.core.types import Turn
from moralstack.models.delib_context import DelibContext
from moralstack.orchestration.contract import DeveloperContract
from moralstack.prompts import critic_prompt, hindsight_prompt, perspectives_prompt, simulator_prompt
from moralstack.prompts.catalog import (
    NO_CONTEXT,
    PromptConfigError,
    PromptContext,
    PromptSet,
    default_prompt_set,
    load_prompt_set,
)
from moralstack.runtime.modules import message_context
from moralstack.runtime.modules.critic_module import (
    CRITIC_FULL_SYSTEM_PROMPT,
    CriticConfig,
    LLMConstitutionalCritic,
    create_critic,
)
from moralstack.runtime.modules.hindsight_module import (
    HindsightConfig,
    LLMHindsightEvaluator,
    create_hindsight_evaluator,
)
from moralstack.runtime.modules.perspective_module import (
    PERSPECTIVE_SYSTEM_PROMPT,
    PERSPECTIVES_BY_ID,
    EnsembleConfig,
    LLMPerspectiveEnsemble,
    Perspective,
    _build_default_perspectives,
    create_minimal_ensemble,
    create_perspective_ensemble,
    create_safety_focused_ensemble,
)
from moralstack.runtime.modules.simulator_module import (
    Consequence,
    LLMConsequenceSimulator,
    ScenarioType,
    SimulatorConfig,
    create_simulator,
)
from moralstack.utils.cache import ModuleResultCache, build_context_fingerprint
from tests.prompt_markers import make_marker_dir, make_when_override_dir, marker_for

# --------------------------------------------------------------------------------------
# Fakes and replies
# --------------------------------------------------------------------------------------

_CRITIC_OK = json.dumps({"decision": "PROCEED", "violated_hard": False, "violations": [], "revision_guidance": ""})
_QUICK_OK = json.dumps({"violated": False})
_PERSPECTIVE_OK = json.dumps({"approval_score": 0.7, "concerns": [], "suggestions": [], "rationale": "ok"})
_SIM_OK = json.dumps(
    {
        "consequences": [
            {
                "text": "outcome",
                "likelihood": 0.5,
                "scenario_type": "social_impact",
                "outcome_valence": 0.0,
                "affected_stakeholders": [],
                "harm_type": "none",
                "harm_severity": 0.0,
                "harm_scope": "individual",
                "reversibility": 1.0,
            }
        ]
    }
)
_HIND_SINGLE_OK = json.dumps(
    {
        "safety": 0.5,
        "helpfulness": 0.5,
        "honesty": 0.5,
        "harm_probability": 0.1,
        "benefit_probability": 0.6,
        "confidence": 0.8,
        "rationale": "ok",
    }
)
_HIND_BATCH_OK = json.dumps(
    {
        "evaluations": [
            {
                "scenario_id": sid,
                "safety": 0.5,
                "helpfulness": 0.5,
                "honesty": 0.5,
                "harm_probability": 0.1,
                "benefit_probability": 0.6,
                "confidence": 0.8,
                "rationale": "ok",
            }
            for sid in ("s1", "s2")
        ]
    }
)
_NOT_JSON = "this is definitely not json"


@dataclass
class _Result:
    text: str
    tokens_used: int = 10
    prompt_tokens: int = 5
    completion_tokens: int = 5

    def token_usage_json(self) -> str | None:
        return None


@dataclass
class _Call:
    system: str
    prompt: str
    messages: list[dict[str, str]] | None


class _Policy:
    """Records the exact (system, user) of every call; replies are consumed in order, the last one repeats."""

    model = "test-model"

    def __init__(self, *replies: str, native: bool = True) -> None:
        self._replies = list(replies)
        self.calls: list[_Call] = []
        if native:
            self.generate_messages = self._generate_messages

    def _next(self) -> str:
        return self._replies.pop(0) if len(self._replies) > 1 else self._replies[0]

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _Result:
        self.calls.append(_Call(system=system, prompt=prompt, messages=None))
        return _Result(self._next())

    def _generate_messages(self, *, messages: list[dict[str, str]], config: Any = None, **_kw: Any) -> _Result:
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        self.calls.append(_Call(system=system, prompt=user, messages=[dict(m) for m in messages]))
        return _Result(self._next())


def _constitution() -> Constitution:
    principle = Principle(id="P1", level="soft", priority=50, title="Safety", rule="Be safe")
    return Constitution(core_principles=[principle])


def _hard_constitution() -> Constitution:
    principle = Principle(id="H1", level="hard", priority=100, title="No harm", rule="Never help harm")
    return Constitution(core_principles=[principle])


def _consequences() -> list[Consequence]:
    return [
        Consequence(text="one", likelihood=0.5, scenario_id=sid, scenario_type=ScenarioType.SOCIAL_IMPACT)
        for sid in ("s1", "s2")
    ]


_CONTRACT = DeveloperContract.from_text("If the user says PING reply PONG.")
_HISTORY = [Turn(role="user", content="earlier question"), Turn(role="assistant", content="earlier answer")]


def _uniq(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


@pytest.fixture(scope="module")
def marker_set(tmp_path_factory: pytest.TempPathFactory) -> PromptSet:
    """A valid non-default set: every packaged fragment carries its own ``<<M:id>>`` marker."""
    return load_prompt_set(make_marker_dir(tmp_path_factory.mktemp("a2b_markers")))


def _has_all(text: str, ids: list[str]) -> None:
    missing = [fid for fid in ids if marker_for(fid) not in text]
    assert not missing, f"markers missing on the wire: {missing}"


# --------------------------------------------------------------------------------------
# Module runners: (set, contract, history) -> (system seen on the wire, system persisted in the result)
# --------------------------------------------------------------------------------------


def _run_critic(ps: PromptSet | None, contract: Any = None, history: Any = None) -> tuple[str, str]:
    policy = _Policy(_CRITIC_OK)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1), prompt_set=ps)
    report = critic.critique(
        "request", "response", _constitution(), developer_contract=contract, conversation_history=history
    )
    return policy.calls[-1].system, report.system_prompt


def _run_perspectives(ps: PromptSet | None, contract: Any = None, history: Any = None) -> tuple[str, str]:
    policy = _Policy(_PERSPECTIVE_OK)
    ensemble = LLMPerspectiveEnsemble(
        policy=policy,
        config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False),
        prompt_set=ps,
    )
    result = ensemble.evaluate("request", "response", developer_contract=contract, conversation_history=history)
    return policy.calls[-1].system, result.system_prompts[0]


def _run_simulator_batch(ps: PromptSet | None, contract: Any = None, history: Any = None) -> tuple[str, str]:
    policy = _Policy(_SIM_OK)
    sim = LLMConsequenceSimulator(
        policy=policy,
        config=SimulatorConfig(max_retries=1, use_seeded_generation=False, enable_caching=False),
        prompt_set=ps,
    )
    result = sim.simulate("request", "response", 2, developer_contract=contract, conversation_history=history)
    return policy.calls[-1].system, result.system_prompt


def _run_hindsight_batch(ps: PromptSet | None, contract: Any = None, history: Any = None) -> tuple[str, str]:
    policy = _Policy(_HIND_BATCH_OK)
    evaluator = LLMHindsightEvaluator(
        policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=False), prompt_set=ps
    )
    result = evaluator.evaluate(
        "request", "response", _consequences(), developer_contract=contract, conversation_history=history
    )
    return policy.calls[-1].system, result.system_prompt


def _run_hindsight_single(ps: PromptSet | None, contract: Any = None, history: Any = None) -> tuple[str, str]:
    policy = _Policy(_HIND_SINGLE_OK)
    evaluator = LLMHindsightEvaluator(
        policy=policy, config=HindsightConfig(max_retries=1, use_batch_evaluation=False, enable_caching=False), prompt_set=ps
    )
    result = evaluator.evaluate("request", "response", _consequences()[:1])
    return policy.calls[-1].system, result.system_prompt


# --------------------------------------------------------------------------------------
# T4.1 marker tests: every module puts ITS set's markers on the wire
# --------------------------------------------------------------------------------------

_CRITIC_FULL_IDS = [
    "critic.base",
    "critic.rules.head",
    "critic.rules.devcontract",
    "critic.rules.tail",
    "common.output_json_only",
]
_PERSPECTIVE_SYSTEM_IDS = [
    "perspectives.base",
    "perspectives.risk_context_interpretation",
    "perspectives.devcontract_evaluation",
    "perspectives.common_instructions",
    "common.output_json_only",
]
_SIM_BATCH_IDS = [
    "simulator.base",
    "simulator.batch.contract_intro",
    "simulator.devcontract_evaluation",
    "simulator.batch.constraints",
    "simulator.harm_type_mapping_rule",
    "simulator.batch.skeleton_heading",
    "simulator.schema_skeleton",
    "simulator.enums",
    "common.output_json_only",
]
_HIND_BATCH_IDS = [
    "hindsight.base",
    "hindsight.batch.instructions",
    "common.output_json_only",
    "hindsight.devcontract_evaluation",
]
_HIND_SINGLE_IDS = ["hindsight.base", "hindsight.single.rubric"]


def test_critic_full_system_and_user_markers(marker_set: PromptSet) -> None:
    policy = _Policy(_CRITIC_OK)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1), prompt_set=marker_set)
    report = critic.critique(
        "request",
        "response",
        _constitution(),
        previous_violations="V1",
        previous_guidance="G1",
    )
    call = policy.calls[0]
    _has_all(call.system, _CRITIC_FULL_IDS)
    _has_all(
        call.prompt, ["critic.full.user", "critic.full.previous_violations_block", "critic.full.previous_guidance_block"]
    )
    assert report.system_prompt == call.system
    assert report.prompt == call.prompt


def test_critic_full_legacy_generate_branch_markers(marker_set: PromptSet) -> None:
    policy = _Policy(_CRITIC_OK, native=False)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1), prompt_set=marker_set)
    critic.critique("request", "response", _constitution())
    _has_all(policy.calls[0].system, _CRITIC_FULL_IDS)
    _has_all(policy.calls[0].prompt, ["critic.full.user"])


def test_critic_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _CRITIC_OK)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=2), prompt_set=marker_set)
    report = critic.critique("request", "response", _constitution())
    assert report.parse_attempts == 2
    _has_all(policy.calls[1].prompt, ["retry.prefix", "critic.retry.body"])
    assert marker_for("critic.retry.body") not in policy.calls[0].prompt


def test_critic_quick_check_markers(marker_set: PromptSet) -> None:
    policy = _Policy(_QUICK_OK, native=False)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1), prompt_set=marker_set)
    result = critic.quick_check("request", "response", _hard_constitution())
    assert result.passed
    _has_all(policy.calls[0].system, ["critic.base"])
    _has_all(policy.calls[0].prompt, ["critic.quick.user"])


def test_perspectives_markers_system_user_and_identity(marker_set: PromptSet) -> None:
    policy = _Policy(_PERSPECTIVE_OK, native=False)
    ensemble = LLMPerspectiveEnsemble(
        policy=policy,
        config=EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False),
        prompt_set=marker_set,
    )
    result = ensemble.evaluate("request", "response")
    call = policy.calls[0]
    _has_all(call.system, _PERSPECTIVE_SYSTEM_IDS)
    _has_all(call.prompt, ["perspectives.identity_block", "perspectives.turn_context", "perspectives.identity.user"])
    assert result.system_prompts == [call.system]


def test_perspectives_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _PERSPECTIVE_OK, native=False)
    ensemble = LLMPerspectiveEnsemble(
        policy=policy,
        config=EnsembleConfig(max_perspectives=1, max_retries=2, parallel_evaluation=False),
        prompt_set=marker_set,
    )
    ensemble.evaluate("request", "response")
    _has_all(policy.calls[1].prompt, ["perspectives.retry"])


def test_simulator_batch_markers(marker_set: PromptSet) -> None:
    policy = _Policy(_SIM_OK)
    sim = LLMConsequenceSimulator(
        policy=policy,
        config=SimulatorConfig(max_retries=1, use_seeded_generation=False, enable_caching=False),
        prompt_set=marker_set,
    )
    result = sim.simulate("request", "response", 2)
    call = policy.calls[0]
    _has_all(call.system, _SIM_BATCH_IDS)
    _has_all(call.prompt, ["simulator.batch.user", "simulator.domain_guidance.default"])
    assert result.system_prompt == call.system


def test_simulator_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _SIM_OK)
    sim = LLMConsequenceSimulator(
        policy=policy,
        config=SimulatorConfig(max_retries=2, use_seeded_generation=False, enable_caching=False),
        prompt_set=marker_set,
    )
    result = sim.simulate("request", "response", 2)
    _has_all(
        policy.calls[1].prompt,
        [
            "retry.prefix",
            "simulator.retry.values_heading",
            "simulator.enums",
            "simulator.schema_skeleton",
            "simulator.retry.footer",
        ],
    )
    assert marker_for("simulator.retry.footer") not in policy.calls[0].prompt
    _has_all(result.prompt, ["retry.prefix"])


def test_hindsight_batch_markers(marker_set: PromptSet) -> None:
    policy = _Policy(_HIND_BATCH_OK)
    evaluator = LLMHindsightEvaluator(
        policy=policy,
        config=HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=False),
        prompt_set=marker_set,
    )
    result = evaluator.evaluate("request", "response", _consequences())
    call = policy.calls[0]
    _has_all(call.system, _HIND_BATCH_IDS)
    _has_all(call.prompt, ["hindsight.batch.user"])
    assert result.system_prompt == call.system


def test_hindsight_batch_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _HIND_BATCH_OK)
    evaluator = LLMHindsightEvaluator(
        policy=policy,
        config=HindsightConfig(max_retries=2, use_batch_evaluation=True, enable_caching=False),
        prompt_set=marker_set,
    )
    evaluator.evaluate("request", "response", _consequences())
    _has_all(policy.calls[1].prompt, ["retry.prefix", "hindsight.retry.batch.body"])


def test_hindsight_single_markers_and_persisted_system(marker_set: PromptSet) -> None:
    policy = _Policy(_HIND_SINGLE_OK, native=False)
    evaluator = LLMHindsightEvaluator(
        policy=policy,
        config=HindsightConfig(max_retries=1, use_batch_evaluation=False, enable_caching=False),
        prompt_set=marker_set,
    )
    result = evaluator.evaluate("request", "response", _consequences()[:1])
    call = policy.calls[0]
    _has_all(call.system, _HIND_SINGLE_IDS)
    _has_all(call.prompt, ["hindsight.single.user"])
    assert result.system_prompt == call.system
    _has_all(result.prompt, ["hindsight.single.user"])


def test_hindsight_single_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _HIND_SINGLE_OK, native=False)
    evaluator = LLMHindsightEvaluator(
        policy=policy, config=HindsightConfig(max_retries=2, enable_caching=False), prompt_set=marker_set
    )
    evaluator.evaluate_scenario("request", "response", _consequences()[0])
    _has_all(policy.calls[1].prompt, ["retry.prefix", "hindsight.retry.single.body"])


def test_message_context_preface_comes_from_the_set(marker_set: PromptSet) -> None:
    messages = message_context.build_module_messages(
        system_prompt="SYS", user_prompt="USER", developer_contract=_CONTRACT, prompt_set=marker_set
    )
    assert messages[-1]["content"].startswith(marker_set.text("context.module_preface"))
    assert marker_for("context.module_preface") in messages[-1]["content"]
    assert messages[-1]["content"].endswith("USER")


# --------------------------------------------------------------------------------------
# Default set: byte identical, and ``prompt_set=None`` is the default
# --------------------------------------------------------------------------------------


def test_message_context_default_preface_is_byte_identical() -> None:
    legacy = message_context._CONTEXT_REFERENCE_INSTRUCTION
    for prompt_set in (None, default_prompt_set()):
        messages = message_context.build_module_messages(
            system_prompt="SYS", user_prompt="USER", developer_contract=_CONTRACT, prompt_set=prompt_set
        )
        assert messages[-1]["content"] == legacy + "USER"
    # no contract and no history: no preface at all (single-turn byte parity)
    plain = message_context.build_module_messages(system_prompt="SYS", user_prompt="USER", prompt_set=None)
    assert plain == [{"role": "system", "content": "SYS"}, {"role": "user", "content": "USER"}]


_CONTEXTS = [(None, None), (_CONTRACT, None), (None, _HISTORY), (_CONTRACT, _HISTORY)]


@pytest.mark.parametrize(
    ("runner", "constant"),
    [
        (_run_critic, lambda: CRITIC_FULL_SYSTEM_PROMPT),
        (
            _run_perspectives,
            lambda: PERSPECTIVE_SYSTEM_PROMPT + "\n\n" + perspectives_prompt.build_perspectives_system_prompt(),
        ),
        (_run_simulator_batch, lambda: simulator_prompt.SIMULATOR_BATCH_SYSTEM_PROMPT),
        (_run_hindsight_batch, lambda: hindsight_prompt.HINDSIGHT_BATCH_SYSTEM_PROMPT),
    ],
)
def test_default_set_system_is_identical_across_every_context(runner: Any, constant: Any) -> None:
    for contract, history in _CONTEXTS:
        for prompt_set in (None, default_prompt_set()):
            wire, persisted = runner(prompt_set, contract, history)
            assert wire == persisted == constant()


# --------------------------------------------------------------------------------------
# ``when`` drop / keep for the module DC copies; prefaces cannot carry a ``when``
# --------------------------------------------------------------------------------------

_DC_FRAGMENTS = [
    ("critic", _run_critic, "critic.rules.devcontract"),
    ("perspectives", _run_perspectives, "perspectives.devcontract_evaluation"),
    ("simulator_batch", _run_simulator_batch, "simulator.devcontract_evaluation"),
    ("hindsight_batch", _run_hindsight_batch, "hindsight.devcontract_evaluation"),
]


@pytest.mark.parametrize(("name", "runner", "fragment_id"), _DC_FRAGMENTS, ids=[d[0] for d in _DC_FRAGMENTS])
@pytest.mark.parametrize("token", ["contract_present", "history_present", "contract_absent", "never"])
def test_dc_fragment_when_drop_keep_and_persisted_system(
    tmp_path: Path, name: str, runner: Any, fragment_id: str, token: str
) -> None:
    ps = load_prompt_set(make_when_override_dir(tmp_path, {fragment_id: token}))
    text = default_prompt_set().text(fragment_id)
    for contract, history in _CONTEXTS:
        ctx = PromptContext.from_contract(contract, history)
        kept = {
            "contract_present": ctx.contract_present,
            "history_present": ctx.history_present,
            "contract_absent": not ctx.contract_present,
            "never": False,
        }[token]
        wire, persisted = runner(ps, contract, history)
        assert (text in wire) is kept, (name, token, ctx)
        assert persisted == wire, "the persisted system prompt is the rendered string that was sent"


def test_module_preface_cannot_carry_a_when(tmp_path: Path) -> None:
    with pytest.raises(PromptConfigError):
        load_prompt_set(make_when_override_dir(tmp_path, {"context.module_preface": "contract_present"}))


def test_critic_quick_check_keeps_its_base_even_when_the_full_rules_are_conditional(tmp_path: Path) -> None:
    ps = load_prompt_set(make_when_override_dir(tmp_path, {"critic.rules.devcontract": "never"}))
    policy = _Policy(_QUICK_OK, native=False)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1), prompt_set=ps)
    critic.quick_check("request", "response", _hard_constitution())
    assert policy.calls[0].system == default_prompt_set().text("critic.quick.system")


# --------------------------------------------------------------------------------------
# T4.9 persisted system_prompt equals the rendered string under an override
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "runner",
    [
        _run_critic,
        _run_perspectives,
        _run_simulator_batch,
        _run_hindsight_batch,
        _run_hindsight_single,
    ],
)
def test_persisted_system_prompt_equals_rendered_under_override(marker_set: PromptSet, runner: Any) -> None:
    wire, persisted = runner(marker_set)
    assert persisted == wire
    assert "<<M:" in persisted
    default_wire, _ = runner(None)
    assert wire != default_wire


# --------------------------------------------------------------------------------------
# T4.4 / T4.5 / T4.6 cache
# --------------------------------------------------------------------------------------


def test_global_cache_not_shared_between_sets(marker_set: PromptSet) -> None:
    # simulator
    request, response = _uniq("sim-req"), _uniq("sim-resp")
    default_policy, other_policy = _Policy(_SIM_OK), _Policy(_SIM_OK)
    config = SimulatorConfig(max_retries=1, use_seeded_generation=False, enable_caching=True)
    first = LLMConsequenceSimulator(default_policy, config).simulate(request, response, 2)
    other_sim = LLMConsequenceSimulator(other_policy, config, prompt_set=marker_set)
    second = other_sim.simulate(request, response, 2)
    assert not first.from_cache and not second.from_cache
    assert len(other_policy.calls) == 1, "a lookup with another set must be a miss"
    assert other_sim.simulate(request, response, 2).from_cache, "the same non-default set still hits its own entry"
    assert LLMConsequenceSimulator(_Policy(_SIM_OK), config).simulate(request, response, 2).from_cache

    # hindsight
    request, response = _uniq("hind-req"), _uniq("hind-resp")
    config_h = HindsightConfig(max_retries=1, use_batch_evaluation=True, enable_caching=True)
    LLMHindsightEvaluator(_Policy(_HIND_BATCH_OK), config_h).evaluate(request, response, _consequences())
    other_h_policy = _Policy(_HIND_BATCH_OK)
    other_h = LLMHindsightEvaluator(other_h_policy, config_h, prompt_set=marker_set)
    assert not other_h.evaluate(request, response, _consequences()).from_cache
    assert len(other_h_policy.calls) == 1
    assert other_h.evaluate(request, response, _consequences()).from_cache

    # perspectives
    request, response = _uniq("persp-req"), _uniq("persp-resp")
    config_p = EnsembleConfig(max_perspectives=1, max_retries=1, parallel_evaluation=False, enable_caching=True)
    LLMPerspectiveEnsemble(_Policy(_PERSPECTIVE_OK), config_p).evaluate(request, response)
    other_p_policy = _Policy(_PERSPECTIVE_OK)
    other_p = LLMPerspectiveEnsemble(other_p_policy, config_p, prompt_set=marker_set)
    assert not other_p.evaluate(request, response).from_cache
    assert len(other_p_policy.calls) == 1
    assert other_p.evaluate(request, response).from_cache


def test_default_cache_keys_byte_identical() -> None:
    """md5 values captured from the code BEFORE ``utils/cache.py`` gained ``prompt_set_tag``.

    The fingerprints come from a duck-typed contract (hash ``abc123``) and a two-turn history; the keys hash
    request ``req`` / response ``resp`` (hindsight additionally ``chash``).
    """
    contract = SimpleNamespace(contract_hash="abc123")
    history = [SimpleNamespace(role="user", content="hello"), SimpleNamespace(role="assistant", content="hi there")]
    expected = {
        "": ("bb24479d16e3100f962fd6ede2f1f6a6", "c120a49afeaa562ded75e2b8eeced56a"),
        "dc:abc123": ("8bbc5fd0aaf86f8648c8e5ad6f649653", "349732607e00861cd4c7c6a364ce6a69"),
        "ch:2cfc22132a9e": ("98adb4c336f8b45ad1ab010013dcf04b", "124a06939c27f6883c4eb6e2480b1dc7"),
        "dc:abc123;ch:2cfc22132a9e": ("fba774345045adff75f52627340b2489", "bb7ac551390ce010c17510fc8edca0c6"),
    }
    cases = {
        "": {},
        "dc:abc123": {"developer_contract": contract},
        "ch:2cfc22132a9e": {"conversation_history": history},
        "dc:abc123;ch:2cfc22132a9e": {"developer_contract": contract, "conversation_history": history},
    }
    cache = ModuleResultCache()
    default_tag = default_prompt_set().cache_tag_for
    for fingerprint, kwargs in cases.items():
        assert build_context_fingerprint(**kwargs) == fingerprint
        assert build_context_fingerprint(**kwargs, prompt_set_tag="") == fingerprint
        ctx = PromptContext.from_contract(kwargs.get("developer_contract"), kwargs.get("conversation_history"))
        assert build_context_fingerprint(**kwargs, prompt_set_tag=default_tag(ctx)) == fingerprint
        plain_key, hindsight_key = expected[fingerprint]
        assert cache._hash_input("req", "resp", fingerprint) == plain_key
        assert cache._hash_input("req", "resp", "chash", fingerprint) == hindsight_key


def test_non_default_tag_changes_key_default_tag_does_not(marker_set: PromptSet, tmp_path: Path) -> None:
    other_set = load_prompt_set(make_marker_dir(tmp_path, marker="<<OTHER:{id}>>"))
    assert marker_set.ps != other_set.ps
    cache = ModuleResultCache()

    def key(tag: str) -> str:
        return cache._hash_input("req", "resp", build_context_fingerprint(prompt_set_tag=tag))

    plain = key("")
    assert plain == cache._hash_input("req", "resp")
    tags = {
        (set_name, c, h): ps.cache_tag_for(PromptContext(c, h))
        for set_name, ps in (("marker", marker_set), ("other", other_set))
        for c in (False, True)
        for h in (False, True)
    }
    assert len(set(tags.values())) == 8, "different sets and different contexts give different tags"
    assert len({key(t) for t in tags.values()} | {plain}) == 9, "and different keys"
    for (_, c, h), tag in tags.items():
        assert re.fullmatch(rf"ps:{marker_set.ps}:{int(c)}{int(h)}|ps:{other_set.ps}:{int(c)}{int(h)}", tag)
    assert default_prompt_set().cache_tag_for(PromptContext(True, True)) == ""


def test_tag_semantics_isolate_the_two_context_collision_cases(marker_set: PromptSet) -> None:
    """A contract built without a hash and a truthy non-iterable history leave no ``dc:``/``ch:`` trace."""
    hashless = SimpleNamespace(raw_text="Reply PONG.", contract_hash="")
    assert build_context_fingerprint(developer_contract=hashless) == build_context_fingerprint() == ""
    assert build_context_fingerprint(conversation_history=5) == ""

    with_contract = marker_set.cache_tag_for(PromptContext.from_contract(hashless, None))
    without = marker_set.cache_tag_for(PromptContext.from_contract(None, None))
    assert with_contract != without
    assert build_context_fingerprint(developer_contract=hashless, prompt_set_tag=with_contract) != build_context_fingerprint(
        prompt_set_tag=without
    )
    with_history = marker_set.cache_tag_for(PromptContext.from_contract(None, 5))
    assert build_context_fingerprint(conversation_history=5, prompt_set_tag=with_history) != build_context_fingerprint(
        prompt_set_tag=without
    )
    # the tag is appended after the dc/ch parts and nothing else changes
    contract = SimpleNamespace(contract_hash="abc123")
    assert build_context_fingerprint(developer_contract=contract, prompt_set_tag="ps:x:10") == "dc:abc123;ps:x:10"
    assert build_context_fingerprint(prompt_set_tag="ps:x:00") == "ps:x:00"


# --------------------------------------------------------------------------------------
# T4.10 evaluate_single uses the instance set
# --------------------------------------------------------------------------------------


def test_evaluate_single_uses_instance_prompt_set(marker_set: PromptSet) -> None:
    policy = _Policy(_PERSPECTIVE_OK, native=False)
    ensemble = LLMPerspectiveEnsemble(policy=policy, config=EnsembleConfig(max_retries=1), prompt_set=marker_set)
    assert ensemble.evaluate_single("request", "response", "user") is not None
    call = policy.calls[0]
    _has_all(call.system, _PERSPECTIVE_SYSTEM_IDS)
    _has_all(call.prompt, ["perspectives.identity_block", "perspectives.turn_context", "perspectives.identity.user"])


def test_evaluate_single_resolves_through_the_set_derived_map_not_the_module_map(marker_set: PromptSet) -> None:
    policy = _Policy(_PERSPECTIVE_OK, native=False)
    only_user = [Perspective(id="user", name="Direct User", prompt_template="custom instructions")]
    ensemble = LLMPerspectiveEnsemble(
        policy=policy, config=EnsembleConfig(max_retries=1), perspectives=only_user, prompt_set=marker_set
    )
    # instance perspectives come first ...
    assert ensemble.evaluate_single("request", "response", "user") is not None
    assert "custom instructions" in policy.calls[0].prompt
    # ... then the defaults built from THIS instance's set
    assert ensemble.evaluate_single("request", "response", "compliance") is not None
    assert marker_for("perspectives.identity.compliance") in policy.calls[1].prompt
    assert marker_for("perspectives.identity.compliance") not in PERSPECTIVES_BY_ID["compliance"].prompt_template
    assert ensemble.evaluate_single("request", "response", "no_such_perspective") is None


def test_set_perspectives_and_factories_use_the_instance_set(marker_set: PromptSet) -> None:
    policy = _Policy(_PERSPECTIVE_OK, native=False)
    config = EnsembleConfig(max_retries=1)
    ensemble = LLMPerspectiveEnsemble(policy=policy, config=config, prompt_set=marker_set)
    ensemble.set_perspectives(["adversary"])
    assert marker_for("perspectives.identity.adversary") in ensemble.perspectives[0].prompt_template

    minimal = create_minimal_ensemble(policy, config, prompt_set=marker_set)
    assert minimal._prompt_set is marker_set
    assert [p.id for p in minimal.perspectives] == ["user", "compliance"]
    assert all("<<M:perspectives.identity." in p.prompt_template for p in minimal.perspectives)
    safety = create_safety_focused_ensemble(policy, prompt_set=marker_set)
    assert safety._prompt_set is marker_set
    assert all("<<M:perspectives.identity." in p.prompt_template for p in safety.perspectives)


# --------------------------------------------------------------------------------------
# T4.7 ``prompt_set=None`` falls back to the default set for every changed constructor / function
# --------------------------------------------------------------------------------------


def _policy_stub() -> _Policy:
    return _Policy("{}")


@pytest.mark.parametrize(
    "factory",
    [
        lambda: LLMConstitutionalCritic(_policy_stub(), config=CriticConfig()),
        lambda: create_critic(_policy_stub(), config=CriticConfig()),
        lambda: LLMConsequenceSimulator(_policy_stub(), SimulatorConfig()),
        lambda: create_simulator(_policy_stub(), SimulatorConfig()),
        lambda: LLMHindsightEvaluator(_policy_stub(), HindsightConfig()),
        lambda: create_hindsight_evaluator(_policy_stub(), HindsightConfig()),
        lambda: LLMPerspectiveEnsemble(_policy_stub(), EnsembleConfig()),
        lambda: create_perspective_ensemble(_policy_stub(), config=EnsembleConfig()),
        lambda: create_minimal_ensemble(_policy_stub(), EnsembleConfig()),
        lambda: create_safety_focused_ensemble(_policy_stub()),
    ],
)
def test_prompt_set_none_falls_back_to_default_for_every_constructor(factory: Any) -> None:
    assert factory()._prompt_set is default_prompt_set()


def test_prompt_set_none_falls_back_to_default_for_every_builder() -> None:
    ds = default_prompt_set()
    ctx = DelibContext(user_prompt="req", draft_text_full="resp")
    assert critic_prompt.build_critic_prompt(ctx, "P", "V", "G") == critic_prompt.build_critic_prompt(
        ctx, "P", "V", "G", prompt_set=ds
    )
    assert simulator_prompt.build_simulator_prompt(ctx, 3) == simulator_prompt.build_simulator_prompt(ctx, 3, prompt_set=ds)
    assert hindsight_prompt.build_hindsight_prompt(ctx, "C") == hindsight_prompt.build_hindsight_prompt(
        ctx, "C", prompt_set=ds
    )
    assert perspectives_prompt.build_perspectives_user_prompt("N", "I", ctx) == (
        perspectives_prompt.build_perspectives_user_prompt("N", "I", ctx, prompt_set=ds)
    )
    system = perspectives_prompt.build_perspectives_system_prompt()
    assert system == perspectives_prompt.build_perspectives_system_prompt(prompt_set=ds, ctx=NO_CONTEXT)
    assert system == ds.render("perspectives.system_body", NO_CONTEXT)
    assert _build_default_perspectives() == _build_default_perspectives(ds)
    plain = message_context.build_module_messages(system_prompt="S", user_prompt="U", developer_contract=_CONTRACT)
    assert plain == message_context.build_module_messages(
        system_prompt="S", user_prompt="U", developer_contract=_CONTRACT, prompt_set=ds
    )


def test_builders_read_the_given_set(marker_set: PromptSet) -> None:
    ctx = DelibContext(user_prompt="req", draft_text_full="resp")
    _has_all(critic_prompt.build_critic_prompt(ctx, "P", "V", "G", prompt_set=marker_set), ["critic.full.user"])
    _has_all(simulator_prompt.build_simulator_prompt(ctx, 3, prompt_set=marker_set), ["simulator.batch.user"])
    _has_all(hindsight_prompt.build_hindsight_prompt(ctx, "C", prompt_set=marker_set), ["hindsight.batch.user"])
    _has_all(
        perspectives_prompt.build_perspectives_user_prompt("N", "I", ctx, prompt_set=marker_set),
        ["perspectives.identity_block", "perspectives.turn_context"],
    )
    _has_all(perspectives_prompt.build_perspectives_system_prompt(prompt_set=marker_set), _PERSPECTIVE_SYSTEM_IDS[1:])


def test_critic_legacy_generate_retry_text_comes_from_the_set(marker_set: PromptSet) -> None:
    policy = _Policy(_NOT_JSON, _CRITIC_OK, native=False)
    critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=2), prompt_set=marker_set)
    report = critic.critique("request", "response", _constitution())
    assert report.parse_attempts == 2
    assert all(call.messages is None for call in policy.calls)
    _has_all(policy.calls[1].prompt, ["retry.prefix", "critic.retry.body"])
    _has_all(policy.calls[1].system, _CRITIC_FULL_IDS)

"""
The critic reads only the first N chars of each principle rule.

N is `CriticConfig.max_rule_len` (default 512, env
`MORALSTACK_CRITIC_MAX_RULE_LEN`). These tests lock the two call sites that
serialize principles into a critic prompt — the full critique and the
fast-path quick-check — to that config value, so the window cannot silently
go back to being hardcoded.

Why it matters: a rule longer than the window is cut mid-sentence, and English
drafting puts carve-outs last. A clause such as "a missing disclaimer does not
violate this principle" then never reaches the judgement that decides
`violated_hard`. Measured at the historical 180, which is why the default is
now 512: no rule shipped today is longer than that.

Offline: the policy is a double, no network.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from moralstack.constitution.schema import Constitution, Principle
from moralstack.runtime.modules.critic_module import CriticConfig, LLMConstitutionalCritic

# 300 chars: the carve-out lives past a 180-char window (the historical default).
_PROHIBITION = (
    "Never provide operational instructions for violent crime, including planning, "
    "target selection, tooling or escape strategies, regardless of the stated motive "
    "of the requester or the framing used."
)
_CARVE_OUT = (
    " Historical or journalistic narration of past events is NOT a violation of this principle and must not be refused."
)
_LONG_RULE = _PROHIBITION + _CARVE_OUT

_CRITIC_PROCEED = json.dumps({"decision": "PROCEED", "violated_hard": False, "violations": [], "revision_guidance": ""})
_QUICK_CHECK_PASS = json.dumps({"violated": False})


@dataclass
class _GenResult:
    text: str


class _CapturingPolicy:
    """Minimal PolicyLLMProtocol double: records every prompt sent."""

    model = "test-model"

    def __init__(self, response: str) -> None:
        self.prompts: list[str] = []
        self._response = response

    def generate(self, *, prompt: str, system: str = "", config: Any = None, **_kw: Any) -> _GenResult:
        self.prompts.append(prompt)
        return _GenResult(text=self._response)


def _hard_principle() -> Principle:
    return Principle(id="TEST.LONG.1", level="hard", priority=100, title="Long rule", rule=_LONG_RULE)


def _constitution() -> Constitution:
    return Constitution(core_principles=[_hard_principle()])


class TestCritiqueRuleWindow:
    """Full critique — critic_module.critique()."""

    def test_narrow_window_cuts_the_carve_out(self):
        policy = _CapturingPolicy(_CRITIC_PROCEED)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1, max_rule_len=180))

        critic.critique("a request", "a draft", _constitution())

        assert len(policy.prompts) == 1
        sent = policy.prompts[0]
        assert "TEST.LONG.1 [H]:" in sent
        assert "..." in sent
        assert "is NOT a violation of this principle" not in sent

    def test_wide_window_keeps_the_carve_out(self):
        policy = _CapturingPolicy(_CRITIC_PROCEED)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1, max_rule_len=512))

        critic.critique("a request", "a draft", _constitution())

        sent = policy.prompts[0]
        assert _LONG_RULE in sent
        assert "is NOT a violation of this principle" in sent


class TestQuickCheckRuleWindow:
    """Fast-path quick-check — critic_module.quick_check(), hard principles only."""

    def test_narrow_window_cuts_the_carve_out(self):
        policy = _CapturingPolicy(_QUICK_CHECK_PASS)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1, max_rule_len=180))

        critic.quick_check("a request", "a draft", _constitution(), [_hard_principle()])

        assert len(policy.prompts) == 1
        sent = policy.prompts[0]
        assert "TEST.LONG.1 [H]:" in sent
        assert "is NOT a violation of this principle" not in sent

    def test_wide_window_keeps_the_carve_out(self):
        policy = _CapturingPolicy(_QUICK_CHECK_PASS)
        critic = LLMConstitutionalCritic(policy=policy, config=CriticConfig(max_retries=1, max_rule_len=512))

        critic.quick_check("a request", "a draft", _constitution(), [_hard_principle()])

        assert "is NOT a violation of this principle" in policy.prompts[0]


def test_shipped_constitution_longest_rule_fits_in_512():
    """512 is not arbitrary: it must cover the longest rule actually shipped.

    If a future edit pushes a rule past 512, this fails and the value in
    .env.template / docs has to be revisited rather than silently truncating.
    """
    from pathlib import Path

    from moralstack.constitution.store import ConstitutionStore

    store = ConstitutionStore(use_llm_matching=False)
    lengths = [len((p.rule or "").strip()) for p in store.load_core()]
    overlays_dir = Path(store._config_dir) / store._overlays_dir
    for path in sorted(overlays_dir.glob("*.yaml")):
        lengths += [len((p.rule or "").strip()) for p in store.load_overlay(path.stem).additional_principles]

    assert max(lengths) <= 512


def test_legal_nopractice_carve_out_survives_a_narrow_window():
    """The carve-out of LEGAL.NOPRACTICE.1 must lead the rule, not close it.

    Measured 2026-08-17: at a 180-char window the clause exempting general
    procedural explanations fell outside the prompt, and the critic marked a
    hard violation on "the process for terminating a contract" - exactly the
    exempted case. The default window is now 512, so this test is the guard
    against reintroducing the dependency: the rule must read correctly even if
    MORALSTACK_CRITIC_MAX_RULE_LEN is lowered back to the historical value.
    """
    from moralstack.constitution.prompt_formatter import format_principles_compact
    from moralstack.constitution.store import ConstitutionStore

    store = ConstitutionStore(use_llm_matching=False)
    principle = next(p for p in store.load_overlay("legal").additional_principles if p.id == "LEGAL.NOPRACTICE.1")

    sent = format_principles_compact([principle], max_rule_len=180)

    assert "are NOT unauthorized practice" in sent

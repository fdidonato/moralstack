"""Prompt-set threading for the retrieval layer (A2e): store, retriever, prefilter and both domain agents.

Marker sets (``tests/prompt_markers.py``) prove which set ended up on the wire. The default-set wires are
pinned byte-for-byte by ``tests/prompts_golden``; here we only prove the override reaches the model and that
``prompt_set=None`` falls back to the packaged defaults. Offline: ``openai.OpenAI`` is replaced by the
recording fake of the golden suite.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import (
    ConstitutionRetrieverConfig,
    DomainAgent,
    DomainPrefilter,
    EnhancedDomainAgent,
)
from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig
from moralstack.prompts.catalog import default_prompt_set, load_prompt_set
from tests.prompt_markers import make_marker_dir, marker_for
from tests.prompts_golden.capture import _principles
from tests.prompts_golden.fakes import FakeOpenAIFactory

_CFG = OpenAIClientConfig(api_key="threading-offline-key", model="gpt-4o")
_QUERY = "How do I synthesize a controlled substance at home?"
_PREFILTER_REPLY = json.dumps({"payload": "p", "selections": [], "wrapper_ignored": []})
_ENHANCED_REPLY = json.dumps({"domain_match": False, "confidence": 0.0, "principle_ids": [], "reasoning": "x"})
_LEGACY_REPLY = json.dumps({"principle_ids": []})


@pytest.fixture
def marker_set(tmp_path: Path) -> Any:
    return load_prompt_set(str(make_marker_dir(tmp_path)))


def _system_and_user(factory: FakeOpenAIFactory) -> tuple[str, str]:
    (call,) = factory.wire
    return call.messages[0]["content"], call.messages[1]["content"]


def _run_prefilter(prompt_set: Any) -> tuple[str, str]:
    factory = FakeOpenAIFactory(_PREFILTER_REPLY)
    with mock.patch("openai.OpenAI", factory):
        pf = DomainPrefilter(
            openai_config=_CFG,
            domain_descriptions={"alpha": "Alpha scope. NOT for: beta (use beta)."},
            prompt_set=prompt_set,
        )
        pf.filter_domains(_QUERY, ["core", "alpha"])
    return _system_and_user(factory)


def _run_enhanced(prompt_set: Any, domain: str = "financial") -> tuple[str, str]:
    factory = FakeOpenAIFactory(_ENHANCED_REPLY)
    with mock.patch("openai.OpenAI", factory):
        EnhancedDomainAgent(
            domain, _principles(), openai_config=_CFG, domain_description="Desc.", prompt_set=prompt_set
        ).evaluate("A query about money")
    return _system_and_user(factory)


def _run_legacy(prompt_set: Any) -> tuple[str, str]:
    factory = FakeOpenAIFactory(_LEGACY_REPLY)
    with mock.patch("openai.OpenAI", factory):
        DomainAgent("legal", _principles(), openai_config=_CFG, prompt_set=prompt_set).evaluate("A query about law")
    return _system_and_user(factory)


# -- marker tests: the override reaches the wire --------------------------------------------------


def test_prefilter_wire_carries_the_marker_set(marker_set: Any) -> None:
    system, user = _run_prefilter(marker_set)
    assert marker_for("retrieval.prefilter.system") in system
    assert marker_for("retrieval.prefilter.user") in user
    # the structural placeholders still render (domain list and cap)
    assert "- alpha: Alpha scope." in system
    assert "never select more than 3" in system


def test_enhanced_agent_wire_carries_the_marker_set(marker_set: Any) -> None:
    system, user = _run_enhanced(marker_set)
    assert marker_for("retrieval.agent.enhanced.system") in system
    assert marker_for("retrieval.agent.enhanced.user") in user
    assert marker_for("retrieval.agent.negative_section") in user


def test_legacy_agent_wire_carries_the_marker_set(marker_set: Any) -> None:
    system, user = _run_legacy(marker_set)
    assert marker_for("retrieval.agent.legacy.system") in system
    assert marker_for("retrieval.agent.legacy.user") in user


@pytest.mark.parametrize("domain", ["financial", "research", "journalism", "enterprise", "mental_health", "medical"])
def test_negative_examples_come_from_the_set_under_an_override(marker_set: Any, domain: str) -> None:
    _, user = _run_enhanced(marker_set, domain)
    assert f"- {marker_for(f'retrieval.agent.negative_examples.{domain}')}" in user
    # the class attribute is only a default re-export: the override must not leak into it
    assert marker_for(f"retrieval.agent.negative_examples.{domain}") not in "".join(
        EnhancedDomainAgent.DOMAIN_NEGATIVE_EXAMPLES[domain]
    )


def test_domain_without_negative_examples_has_no_negative_section(marker_set: Any) -> None:
    _, user = _run_enhanced(marker_set, "legal")
    assert marker_for("retrieval.agent.negative_section") not in user
    assert "NEGATIVE EXAMPLES" not in user


def test_domain_agent_cache_key_follows_the_prompt_text(marker_set: Any) -> None:
    """Same query, different set: the agent cache (keyed on the prompt text) must not be shared."""
    default_agent = EnhancedDomainAgent("financial", _principles(), openai_config=_CFG)
    marker_agent = EnhancedDomainAgent("financial", _principles(), openai_config=_CFG, prompt_set=marker_set)
    factory = FakeOpenAIFactory(_ENHANCED_REPLY)
    with mock.patch("openai.OpenAI", factory):
        default_agent.evaluate("same query")
        marker_agent.evaluate("same query")
    assert set(default_agent._cache) != set(marker_agent._cache)
    assert len(factory.wire) == 2


# -- prompt_set=None falls back to the packaged defaults --------------------------------------------


def test_prompt_set_none_falls_back_to_default_for_every_retrieval_component() -> None:
    ds = default_prompt_set()
    assert DomainPrefilter(openai_config=_CFG)._prompt_set is ds
    assert EnhancedDomainAgent("financial", _principles(), openai_config=_CFG)._prompt_set is ds
    assert DomainAgent("legal", _principles(), openai_config=_CFG)._prompt_set is ds
    assert ConstitutionRetrieverConfig().prompt_set is None
    assert ConstitutionStoreConfig().prompt_set is None


def test_default_wire_has_no_marker_and_matches_explicit_default_set() -> None:
    assert _run_prefilter(None) == _run_prefilter(default_prompt_set())
    assert _run_enhanced(None) == _run_enhanced(default_prompt_set())
    assert _run_legacy(None) == _run_legacy(default_prompt_set())
    assert "<<M:" not in "".join(_run_prefilter(None) + _run_enhanced(None) + _run_legacy(None))


# -- store: kwargs path, config path, config wins ----------------------------------------------------


def _store(**kwargs: Any) -> ConstitutionStore:
    return ConstitutionStore(use_llm_matching=False, **kwargs)


def _assert_store_threads(store: ConstitutionStore, expected: Any) -> None:
    retriever = store._retriever
    assert retriever._config.prompt_set is expected
    assert retriever._domain_prefilter is not None
    assert retriever._domain_prefilter._prompt_set is expected
    enhanced = retriever._create_enhanced_agents(["core", *store.get_available_domains()[:2]])
    assert enhanced
    assert all(agent._prompt_set is expected for agent in enhanced)
    legacy = retriever._create_domain_agents()
    assert legacy
    assert all(agent._prompt_set is expected for agent in legacy)


def test_constitution_store_kwarg_and_config_threading(marker_set: Any, tmp_path: Path) -> None:
    other_set = load_prompt_set(str(make_marker_dir(tmp_path, marker="<<OTHER:{id}>>")))
    assert other_set is not marker_set

    # kwargs path (benchmark style)
    _assert_store_threads(_store(prompt_set=marker_set), marker_set)
    # config path
    _assert_store_threads(
        ConstitutionStore(ConstitutionStoreConfig(use_llm_matching=False, prompt_set=marker_set)), marker_set
    )
    # config wins over the kwarg when both are given
    both = ConstitutionStore(
        ConstitutionStoreConfig(use_llm_matching=False, prompt_set=marker_set),
        prompt_set=other_set,
    )
    _assert_store_threads(both, marker_set)
    # neither: defaults (None is propagated, components resolve the packaged set)
    plain = _store()
    assert plain._retriever._config.prompt_set is None
    assert plain._retriever._domain_prefilter is not None
    assert plain._retriever._domain_prefilter._prompt_set is default_prompt_set()


def test_store_retrieval_puts_the_marker_on_prefilter_and_agent_wires(marker_set: Any) -> None:
    store = _store(prompt_set=marker_set, openai_config=_CFG)
    prefilter_factory = FakeOpenAIFactory(
        json.dumps(
            {
                "payload": "p",
                "selections": [{"domain": "medical", "evidence": "e", "confidence": 0.9}],
                "wrapper_ignored": [],
            }
        )
    )
    with mock.patch("openai.OpenAI", prefilter_factory):
        store.get_relevant_principles(_QUERY, top_k=5)
    wires = prefilter_factory.wire
    assert len(wires) >= 2  # the prefilter and at least one domain agent
    systems = [c.messages[0]["content"] for c in wires]
    users = [c.messages[1]["content"] for c in wires]
    assert any(marker_for("retrieval.prefilter.system") in s for s in systems)
    assert any(marker_for("retrieval.prefilter.user") in u for u in users)
    assert any(marker_for("retrieval.agent.enhanced.system") in s for s in systems)
    assert any(marker_for("retrieval.agent.enhanced.user") in u for u in users)

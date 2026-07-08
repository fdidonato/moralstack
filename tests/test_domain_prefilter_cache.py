"""Domain prefilter cache idempotence, observability events, and regression guards."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from moralstack.constitution.openai_config import OpenAIClientConfig
from moralstack.constitution.retriever import (
    DomainAgent,
    DomainPrefilter,
    EnhancedDomainAgent,
    _fingerprint_domain_keywords,
    _normalize_domain_keywords,
)
from moralstack.constitution.schema import Principle
from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.context import set_current_request_id, set_current_run_id
from moralstack.observability.read_store import SqliteReadStore
from moralstack.observability.service import get_obs
from moralstack.observability.sinks.sqlite_sink import create_run, init_db, upsert_request
from moralstack.orchestration.orchestration_event_taxonomy import (
    DOMAIN_PREFILTER_CACHE_HIT,
    DOMAIN_PREFILTER_CACHE_INVALIDATED,
    DOMAIN_PREFILTER_CACHE_MISS,
)
from moralstack.reports.runtime_decisions import (
    build_retrieval_reuse_summary,
    build_runtime_decision_observability,
)

_rs = SqliteReadStore()
get_orchestration_events_for_request = _rs.get_orchestration_events_for_request

_CONFIG_CORE = Path(__file__).resolve().parent.parent / "moralstack" / "constitution" / "data" / "core.yaml"


@pytest.fixture(autouse=True)
def _fresh_obs_singleton():
    """Reset the obs service + sink singletons around each test.

    After P2 the worker owns a long-lived SQLite connection bound to the db_path
    seen at first use; without this reset a stale worker connection (from another
    test or the session :memory: fixture) would write children into the wrong DB
    and trip foreign_keys=ON. Resetting binds a fresh worker to the per-test DB.
    """
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None
    yield
    try:
        get_obs().shutdown(timeout=1.0)
    except Exception:
        pass
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


def _principle(*, title: str = "Title", rule: str = "Rendered rule") -> Principle:
    return Principle(
        id="TEST.P1",
        level="hard",
        priority=90,
        title=title,
        rule=rule,
    )


def test_normalize_keywords_order_invariant():
    a = {"b": ["z", "a", "a"], "a": ["m"]}
    b = {"a": ["m"], "b": ["a", "z"]}
    assert _normalize_domain_keywords(a) == _normalize_domain_keywords(b)
    assert _fingerprint_domain_keywords(a) == _fingerprint_domain_keywords(b)


def test_set_domain_keywords_idempotent_preserves_cache():
    kw = {"core": ["c"], "dom": ["x", "y"]}
    p = DomainPrefilter(domain_keywords=kw, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["dom"], "confidence": 0.9}):
        p.filter_domains("same query", ["core", "dom"])
    assert len(p._cache) == 1
    assert p.set_domain_keywords({"dom": ["y", "x"], "core": ["c"]}) is False
    assert len(p._cache) == 1


def test_set_domain_keywords_change_clears_cache():
    kw = {"core": ["c"], "dom": ["x"]}
    p = DomainPrefilter(domain_keywords=kw, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["dom"], "confidence": 0.9}):
        p.filter_domains("query about banking", ["core", "dom"])
    assert len(p._cache) == 1
    fp_before = p._keywords_fingerprint
    assert p.set_domain_keywords({"core": ["c"], "dom": ["z"]}) is True
    assert len(p._cache) == 0
    assert p._keywords_fingerprint != fp_before


def test_snapshot_isolation_from_provider_mutation():
    shared: dict[str, list[str]] = {"d": ["one"]}
    p = DomainPrefilter(domain_keywords=shared, max_domains=3)
    shared["d"].append("two")
    assert p.set_domain_keywords({"d": ["one"]}) is False
    assert p._domain_keywords["d"] == ["one"]


def test_repeated_filter_domains_single_openai_call():
    kw = {"core": ["c"], "dom": ["x"]}
    p = DomainPrefilter(domain_keywords=kw, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["dom"], "confidence": 0.9}) as m:
        p.filter_domains("money advice question", ["core", "dom"])
        p.set_domain_keywords(kw)
        p.filter_domains("money advice question", ["core", "dom"])
    assert m.call_count == 1


def _fake_completion_json_obj(content: str) -> MagicMock:
    """Mirrors tests/test_runtime_pooling.py::_fake_completion_json_obj (kept local, minimal)."""
    msg = MagicMock()
    msg.content = content
    ch = MagicMock()
    ch.message = msg
    resp = MagicMock()
    resp.choices = [ch]
    resp.usage = None
    return resp


def test_call_openai_persists_system_prompt_as_built_block_and_prompt_as_query_only():
    """Single source (task decision 4): the builder output feeds BOTH the API system
    message and the persisted system_prompt; the persisted prompt is query-only."""
    kw = {"core": ["c"], "dom": ["x"]}
    p = DomainPrefilter(
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini"),
        domain_keywords=kw,
        max_domains=3,
    )
    payload = json.dumps({"domains": ["dom"], "confidence": 0.9})
    query = "question about banking regulations"
    captured: dict = {}

    def _capture_persist(**kwargs):
        captured.update(kwargs)

    with (
        patch("openai.OpenAI") as ctor,
        patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_capture_persist),
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=_fake_completion_json_obj(payload))
        ctor.return_value = mock_client

        result = p.filter_domains(query, ["core", "dom"])

    assert result == ["core", "dom"]
    domain_list = "\n".join(["- dom: x"])
    expected_system_prompt = p._build_prefilter_system_prompt(domain_list)
    assert captured["system_prompt"] == expected_system_prompt
    assert captured["prompt"] == f"USER QUERY:\n{query}"
    # Also lock the outbound OpenAI system message to the same single source.
    sent_messages = mock_client.chat.completions.create.call_args.kwargs["messages"]
    assert sent_messages[0] == {"role": "system", "content": expected_system_prompt}
    assert sent_messages[1] == {"role": "user", "content": f"USER QUERY:\n{query}"}


def test_call_openai_strict_json_parse_path():
    """Valid JSON content -> data parsed directly, no fallback."""
    p = DomainPrefilter(openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini"))
    payload = json.dumps({"domains": ["core"], "confidence": 0.9})
    captured: dict = {}

    def _capture_persist(**kwargs):
        captured.update(kwargs)

    with (
        patch("openai.OpenAI") as ctor,
        patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_capture_persist),
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=_fake_completion_json_obj(payload))
        ctor.return_value = mock_client
        data = p._call_openai("USER QUERY:\nquery", system_prompt="sys")

    assert data == {"domains": ["core"], "confidence": 0.9}
    summary = json.loads(captured["parsed_summary_json"])
    assert summary["parse_contract"]["parse_status"] == "ok"
    assert summary["parse_contract"]["fallback_used"] is False


def test_call_openai_regex_fallback_on_malformed_json():
    """JSON wrapped in surrounding prose -> tolerant recovery, fallback_used True."""
    p = DomainPrefilter(openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini"))
    recovered = {"domains": ["core"], "confidence": 0.7}
    text = f"Here is the classification:\n{json.dumps(recovered)}\nThanks."
    captured: dict = {}

    def _capture_persist(**kwargs):
        captured.update(kwargs)

    with (
        patch("openai.OpenAI") as ctor,
        patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_capture_persist),
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=_fake_completion_json_obj(text))
        ctor.return_value = mock_client
        data = p._call_openai("USER QUERY:\nquery", system_prompt="sys")

    assert data == recovered
    summary = json.loads(captured["parsed_summary_json"])
    assert summary["parse_contract"]["parse_status"] == "fallback_ok"
    assert summary["parse_contract"]["fallback_used"] is True


def test_call_openai_fully_unparseable_returns_empty_and_failed_status():
    """No recoverable JSON object at all -> empty dict, failed status."""
    p = DomainPrefilter(openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-4o-mini"))
    text = "I cannot help with that request."
    captured: dict = {}

    def _capture_persist(**kwargs):
        captured.update(kwargs)

    with (
        patch("openai.OpenAI") as ctor,
        patch("moralstack.constitution.retriever.persist_llm_call", side_effect=_capture_persist),
    ):
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=_fake_completion_json_obj(text))
        ctor.return_value = mock_client
        data = p._call_openai("USER QUERY:\nquery", system_prompt="sys")

    assert data == {}
    summary = json.loads(captured["parsed_summary_json"])
    assert summary["parse_contract"]["parse_status"] == "failed"
    assert summary["parse_contract"]["fallback_used"] is False


def test_miss_then_hit_emits_events(tmp_path, monkeypatch):
    dbp = str(tmp_path / "pf.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r-pf", run_type="test", meta={})
    assert upsert_request("r-pf", "q-pf", prompt="p", domain="")
    set_current_run_id("r-pf")
    set_current_request_id("q-pf")

    kw = {"core": ["c"], "dom": ["x"]}
    p = DomainPrefilter(domain_keywords=kw, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["dom"], "confidence": 0.9}):
        p.filter_domains("qq long enough query", ["core", "dom"])
        p.set_domain_keywords(kw)
        p.filter_domains("qq long enough query", ["core", "dom"])

    get_obs().flush(timeout=5.0)
    rows = get_orchestration_events_for_request("r-pf", "q-pf")
    types = [r.get("event_type") for r in rows]
    assert types.count(DOMAIN_PREFILTER_CACHE_MISS) >= 1
    assert types.count(DOMAIN_PREFILTER_CACHE_HIT) >= 1
    assert DOMAIN_PREFILTER_CACHE_INVALIDATED not in types


def test_invalidate_only_when_keywords_change(tmp_path, monkeypatch):
    dbp = str(tmp_path / "pf2.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r2", run_type="test", meta={})
    assert upsert_request("r2", "q2", prompt="p", domain="")
    set_current_run_id("r2")
    set_current_request_id("q2")

    kw = {"core": ["c"], "dom": ["x"]}
    p = DomainPrefilter(domain_keywords=kw, max_domains=3)
    p.set_domain_keywords(kw)
    get_obs().flush(timeout=5.0)
    rows = get_orchestration_events_for_request("r2", "q2")
    assert not any(r.get("event_type") == DOMAIN_PREFILTER_CACHE_INVALIDATED for r in rows)

    p.set_domain_keywords({"core": ["c"], "dom": ["y"]})
    get_obs().flush(timeout=5.0)
    rows2 = get_orchestration_events_for_request("r2", "q2")
    assert any(r.get("event_type") == DOMAIN_PREFILTER_CACHE_INVALIDATED for r in rows2)


def test_request_analysis_trace_prefilter_fields():
    traces = [
        {
            "stage": "REQUEST_ANALYSIS_CONTEXT",
            "sequence": 99,
            "trace_json": json.dumps(
                {
                    "request_id": "x",
                    "stage": "REQUEST_ANALYSIS_CONTEXT",
                    "stage_payload": {
                        "retrieval_count": 3,
                        "prefilter_cache_status": "hit",
                        "prefilter_cache_reason": None,
                        "prefilter_keywords_changed": False,
                        "prefilter_keywords_fingerprint_prefix": "abcd1234",
                    },
                }
            ),
        }
    ]
    vm = build_runtime_decision_observability(traces=traces, orchestration_events=[])
    rac = vm["execution_strategy"]["request_analysis_context"]
    assert rac.get("prefilter_cache_status") == "hit"
    assert rac.get("prefilter_keywords_fingerprint_prefix") == "abcd1234"
    summ = build_retrieval_reuse_summary(traces, [])
    assert summ.get("prefilter_cache_status") == "hit"


@pytest.mark.skipif(not _CONFIG_CORE.exists(), reason="moralstack/constitution/data/core.yaml not present")
def test_domain_selection_stable_pre_post_idempotence():
    """Regression: identical inputs yield identical prefilter domain lists with mocked LLM."""
    from moralstack.constitution.store import ConstitutionStore

    config_dir = _CONFIG_CORE.parent

    def _mock_prefilter_openai(prompt: str) -> dict:
        return {"domains": [], "confidence": 0.0}

    store = ConstitutionStore(config_dir=config_dir, use_enhanced_retrieval=True, use_domain_prefilter=True)
    q = "neutral test query for domain prefilter"
    avail = ["core"] + store.get_available_domains()
    with patch("moralstack.constitution.retriever.DomainPrefilter._call_openai", side_effect=_mock_prefilter_openai):
        d1 = store.detect_relevant_domains(q)
        d2 = store.detect_relevant_domains(q)
    assert d1 == d2
    assert all(x in avail for x in d1)


def test_clear_cache_forced_emits_when_non_empty(tmp_path, monkeypatch):
    dbp = str(tmp_path / "pf3.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r3", run_type="test", meta={})
    assert upsert_request("r3", "q3", prompt="p", domain="")
    set_current_run_id("r3")
    set_current_request_id("q3")

    p = DomainPrefilter(domain_keywords={"core": ["a"]}, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": [], "confidence": 0.0}):
        p.filter_domains("z long enough query", ["core"])
    p.clear_cache(reason="forced_refresh")
    get_obs().flush(timeout=5.0)
    rows = get_orchestration_events_for_request("r3", "q3")
    assert any(r.get("event_type") == DOMAIN_PREFILTER_CACHE_INVALIDATED for r in rows)


def test_short_query_bypasses_llm_and_returns_empty():
    """Queries < MIN_QUERY_LEN_FOR_CLASSIFICATION carry too little signal:
    the prefilter must skip the LLM round-trip and return an empty list so
    the caller's default (core principles) applies."""
    p = DomainPrefilter(domain_keywords={"core": ["c"], "dom": ["x"]}, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai") as m:
        result = p.filter_domains("51", ["core", "dom"])
        result2 = p.filter_domains("        ", ["core", "dom"])  # whitespace only
        result3 = p.filter_domains("nine char", ["core", "dom"])  # 9 chars
    assert result == []
    assert result2 == []
    assert result3 == []
    assert m.call_count == 0, "LLM must not be invoked for queries below the length threshold"


def test_short_query_emits_too_short_event(tmp_path, monkeypatch):
    """The bypass must emit DOMAIN_PREFILTER_QUERY_TOO_SHORT with rationale."""
    from moralstack.orchestration.orchestration_event_taxonomy import (
        DOMAIN_PREFILTER_QUERY_TOO_SHORT,
    )

    dbp = str(tmp_path / "pf_short.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r-short", run_type="test", meta={})
    assert upsert_request("r-short", "q-short", prompt="p", domain="")
    set_current_run_id("r-short")
    set_current_request_id("q-short")

    p = DomainPrefilter(domain_keywords={"core": ["c"], "dom": ["x"]}, max_domains=3)
    p.filter_domains("63312", ["core", "dom"])

    get_obs().flush(timeout=5.0)
    rows = get_orchestration_events_for_request("r-short", "q-short")
    matches = [r for r in rows if r.get("event_type") == DOMAIN_PREFILTER_QUERY_TOO_SHORT]
    assert matches, "DOMAIN_PREFILTER_QUERY_TOO_SHORT event must be persisted"
    payload = json.loads(matches[0].get("payload_json") or "{}")
    assert payload.get("rationale") == "query too short to identify a domain"
    assert payload.get("query_length") == 5
    assert payload.get("threshold") == DomainPrefilter.MIN_QUERY_LEN_FOR_CLASSIFICATION


def test_long_query_still_invokes_llm():
    """Regression guard: queries >= threshold must still hit the LLM classifier."""
    p = DomainPrefilter(domain_keywords={"core": ["c"], "dom": ["x"]}, max_domains=3)
    with patch.object(DomainPrefilter, "_call_openai", return_value={"domains": ["dom"], "confidence": 0.9}) as m:
        p.filter_domains("question about banking and finance", ["core", "dom"])
    assert m.call_count == 1


def test_clear_cache_noop_when_empty(tmp_path, monkeypatch):
    dbp = str(tmp_path / "pf4.db")
    monkeypatch.setenv("MORALSTACK_DB_PATH", dbp)
    monkeypatch.setenv("MORALSTACK_PERSIST_MODE", "db_only")
    init_db(dbp)
    assert create_run("r4", run_type="test", meta={})
    assert upsert_request("r4", "q4", prompt="p", domain="")
    set_current_run_id("r4")
    set_current_request_id("q4")

    p = DomainPrefilter(domain_keywords={"core": ["a"]}, max_domains=3)
    p.clear_cache(reason="forced_refresh")
    get_obs().flush(timeout=5.0)
    rows = get_orchestration_events_for_request("r4", "q4")
    assert not rows


def test_enhanced_agent_cache_miss_on_rendered_rule_change():
    agent = EnhancedDomainAgent(
        "medical",
        [_principle(rule="Initial rendered rule")],
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-test"),
        domain_description="medical description",
    )

    with patch.object(
        EnhancedDomainAgent,
        "_call_openai",
        side_effect=[
            {"domain_match": True, "confidence": 0.9, "principle_ids": ["TEST.P1"], "reasoning": "first"},
            {"domain_match": True, "confidence": 0.8, "principle_ids": [], "reasoning": "second"},
        ],
    ) as call:
        first = agent.evaluate("same query")
        agent.principles = [_principle(rule="Changed rendered rule")]
        second = agent.evaluate("same query")

    assert call.call_count == 2
    assert first.reasoning == "first"
    assert second.reasoning == "second"


def test_enhanced_agent_cache_miss_on_domain_description_change():
    agent = EnhancedDomainAgent(
        "medical",
        [_principle()],
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-test"),
        domain_description="initial description",
    )

    with patch.object(
        EnhancedDomainAgent,
        "_call_openai",
        side_effect=[
            {"domain_match": True, "confidence": 0.9, "principle_ids": ["TEST.P1"], "reasoning": "first"},
            {"domain_match": True, "confidence": 0.8, "principle_ids": [], "reasoning": "second"},
        ],
    ) as call:
        first = agent.evaluate("same query")
        agent._domain_description = "changed description"
        second = agent.evaluate("same query")

    assert call.call_count == 2
    assert first.reasoning == "first"
    assert second.reasoning == "second"


def test_enhanced_agent_cache_hit_on_unrendered_title_change():
    agent = EnhancedDomainAgent(
        "medical",
        [_principle(title="Initial title", rule="Same rendered rule")],
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-test"),
        domain_description="medical description",
    )

    with patch.object(
        EnhancedDomainAgent,
        "_call_openai",
        return_value={"domain_match": True, "confidence": 0.9, "principle_ids": ["TEST.P1"], "reasoning": "cached"},
    ) as call:
        first = agent.evaluate("same query")
        agent.principles = [_principle(title="Changed title", rule="Same rendered rule")]
        second = agent.evaluate("same query")

    assert call.call_count == 1
    assert first is second
    assert second.reasoning == "cached"


def test_legacy_domain_agent_cache_miss_on_rendered_rule_change():
    agent = DomainAgent(
        "core",
        [_principle(rule="Initial rendered rule")],
        openai_config=OpenAIClientConfig(api_key="sk-test", model="gpt-test"),
    )

    with patch.object(DomainAgent, "_call_openai", side_effect=[["TEST.P1"], []]) as call:
        first = agent.evaluate("same query")
        agent.principles = [_principle(rule="Changed rendered rule")]
        second = agent.evaluate("same query")

    assert call.call_count == 2
    assert first == ["TEST.P1"]
    assert second == []

"""Domain prefilter cache idempotence, observability events, and regression guards."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from moralstack.constitution.retriever import (
    DomainPrefilter,
    _fingerprint_domain_keywords,
    _normalize_domain_keywords,
)
from moralstack.orchestration.orchestration_event_taxonomy import (
    DOMAIN_PREFILTER_CACHE_HIT,
    DOMAIN_PREFILTER_CACHE_INVALIDATED,
    DOMAIN_PREFILTER_CACHE_MISS,
)
from moralstack.persistence.context import set_current_request_id, set_current_run_id
from moralstack.persistence.db import create_run, get_orchestration_events_for_request, init_db, upsert_request
from moralstack.reports.runtime_decisions import (
    build_retrieval_reuse_summary,
    build_runtime_decision_observability,
)

_CONFIG_CORE = Path(__file__).resolve().parent.parent / "moralstack" / "constitution" / "data" / "core.yaml"


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
        p.filter_domains("q", ["core", "dom"])
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
        p.filter_domains("money", ["core", "dom"])
        p.set_domain_keywords(kw)
        p.filter_domains("money", ["core", "dom"])
    assert m.call_count == 1


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
        p.filter_domains("qq", ["core", "dom"])
        p.set_domain_keywords(kw)
        p.filter_domains("qq", ["core", "dom"])

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
    rows = get_orchestration_events_for_request("r2", "q2")
    assert not any(r.get("event_type") == DOMAIN_PREFILTER_CACHE_INVALIDATED for r in rows)

    p.set_domain_keywords({"core": ["c"], "dom": ["y"]})
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
        p.filter_domains("z", ["core"])
    p.clear_cache(reason="forced_refresh")
    rows = get_orchestration_events_for_request("r3", "q3")
    assert any(r.get("event_type") == DOMAIN_PREFILTER_CACHE_INVALIDATED for r in rows)


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
    rows = get_orchestration_events_for_request("r4", "q4")
    assert not rows

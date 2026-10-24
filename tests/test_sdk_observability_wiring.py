"""GovernanceConfig observability fields (observability_mode / jsonl_dir / db_path) are wired (known-gaps S5).

Explicit values are process-wide and may only *establish* the observability target (first
``govern()``/``create_app`` before the write-queue target is bound); a later different value is
refused with a WARNING and never redirects a live target (parents and children must stay in one DB).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from moralstack.observability import config as obs_config
from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.events import EVENT_DEBUG_EVENT, EVENT_LLM_CALL, make_envelope
from moralstack.observability.sinks.sqlite_sink import upsert_request
from moralstack.observability.write_queue import ObservabilityWriteQueue
from moralstack.sdk.config import GovernanceConfig
from moralstack.sdk.wrapper import govern
from moralstack.server.proxy import create_app
from tests.test_server_proxy import _make_result, _make_upstream_chat_completion

_CFG_LOGGER = "moralstack.observability.config"
_ENV_KEYS = (
    "MORALSTACK_DB_PATH",
    "MORALSTACK_OBSERVABILITY_DB_PATH",
    "MORALSTACK_OBSERVABILITY_MODE",
    "MORALSTACK_PERSIST_MODE",
    "MORALSTACK_OBSERVABILITY_JSONL_DIR",
)


def _reset_obs_singletons() -> None:
    with contextlib.suppress(Exception):
        service_module.get_obs().shutdown(timeout=2.0)
    service_module._obs_instance = None
    router._sqlite_sink = None
    router._jsonl_sink = None


def _reset_all() -> None:
    _reset_obs_singletons()
    obs_config._reset_explicit_config()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    for key in _ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "env_jsonl"))
    _reset_all()
    yield
    _reset_all()


@pytest.fixture
def env_db(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> str:
    """A file-backed env DB (``:memory:`` opens a fresh DB per connect, so it cannot prove 'same DB')."""
    path = str(tmp_path / "env.db")
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", path)
    return path


def _orch() -> Any:
    orch = MagicMock()
    orch.prompt_set = None
    return orch


def _govern(config: GovernanceConfig | None) -> Any:
    with patch("moralstack.sdk.wrapper._bootstrap_pipeline", return_value=_orch()):
        return govern(MagicMock(), config)


def _app(config: GovernanceConfig | None, *, final_action: str = "NORMAL_COMPLETE") -> TestClient:
    orchestrator = MagicMock()
    orchestrator.process = MagicMock(return_value=_make_result(final_action, content="guidance"))
    orchestrator.prompt_set = None
    upstream = MagicMock()
    upstream.chat.completions.create = MagicMock(return_value=_make_upstream_chat_completion())
    return TestClient(create_app(openai_client=upstream, orchestrator=orchestrator, config=config))


def _governed_call(run_id: str, request_id: str) -> None:
    """Stand-in for one governed call: synchronous parent request row + async child envelope."""
    from moralstack.observability import get_obs

    assert upsert_request(run_id, request_id, "prompt")
    get_obs().emit(
        make_envelope(
            EVENT_LLM_CALL,
            run_id=run_id,
            request_id=request_id,
            payload={"phase": "p", "module": "m", "action": "a", "prompt": "", "raw_response": ""},
        )
    )
    get_obs().flush(timeout=10.0)


def _count(db: str, table: str, run_id: str) -> int:
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)).fetchone()[0]
    finally:
        conn.close()


def _warnings(caplog: pytest.LogCaptureFixture, needle: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == _CFG_LOGGER and r.levelno >= logging.WARNING and needle in r.getMessage()]


def _run_started(jsonl_dir: Path) -> list[dict[str, Any]]:
    path = jsonl_dir / "run.started.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# 1
def test_default_config_changes_nothing(env_db: str) -> None:
    env_before = (obs_config.get_db_path(), obs_config.get_observability_mode(), obs_config.get_jsonl_dir())
    _govern(GovernanceConfig())
    assert obs_config._explicit == {}
    assert (obs_config.get_db_path(), obs_config.get_observability_mode(), obs_config.get_jsonl_dir()) == env_before
    assert env_before[0] == env_db


# 2
def test_file_only_jsonl_dir_overrides_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "db_only")
    env_snapshot = dict(os.environ)
    target = tmp_path / "audit"
    client = _govern(GovernanceConfig(observability_mode="file_only", jsonl_dir=str(target)))
    assert obs_config.get_observability_mode() == "file_only"
    assert obs_config.get_jsonl_dir() == str(target)
    records = _run_started(target)
    assert [r["payload"]["run_type"] for r in records] == ["sdk_session"]
    assert records[0]["run_id"] == client._run_id
    assert _run_started(tmp_path / "env_jsonl") == []
    assert dict(os.environ) == env_snapshot


# 3
def test_explicit_db_path_gets_run_and_child_rows(env_db: str, tmp_path: Path) -> None:
    explicit = str(tmp_path / "x.db")
    client = _govern(GovernanceConfig(observability_mode="db_only", db_path=explicit))
    assert obs_config.get_db_path() == explicit
    assert _count(explicit, "runs", "x") == 0 and _count(explicit, "runs", client._run_id) == 1
    _governed_call(client._run_id, "req-3")
    assert _count(explicit, "llm_calls", client._run_id) == 1
    assert not Path(env_db).exists()


# 4
def test_db_path_alone_implies_db_only_and_partial_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    explicit = str(tmp_path / "only.db")
    _govern(GovernanceConfig(db_path=explicit))  # mode "off" = inherit
    assert obs_config.get_observability_mode() == "db_only"
    assert obs_config.get_db_path() == explicit

    _reset_all()
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(tmp_path / "from_env"))
    _govern(GovernanceConfig(observability_mode="dual"))
    assert obs_config.get_observability_mode() == "dual"
    assert obs_config.get_jsonl_dir() == str(tmp_path / "from_env")


# 5
def test_second_client_different_jsonl_dir_warns_and_keeps_first(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    first, second = str(tmp_path / "a"), str(tmp_path / "b")
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        _govern(GovernanceConfig(observability_mode="file_only", jsonl_dir=first))
        assert not _warnings(caplog, "jsonl_dir")
        _govern(GovernanceConfig(observability_mode="file_only", jsonl_dir=second))
        assert len(_warnings(caplog, "jsonl_dir")) == 1
        _govern(GovernanceConfig(observability_mode="file_only", jsonl_dir=first))
        assert len(_warnings(caplog, "jsonl_dir")) == 1
    assert obs_config.get_jsonl_dir() == first


# 6
@pytest.mark.parametrize("mode", ["OFF", " off ", None, ""])
def test_off_none_blank_inherit(mode: str | None, tmp_path: Path) -> None:
    before = obs_config.get_observability_mode()
    obs_config.apply_explicit_config(mode=mode, db_path=None, jsonl_dir=None, source="test")
    assert obs_config.get_observability_mode() == before
    assert obs_config._explicit == {}


def test_invalid_mode_warns_and_inherits(caplog: pytest.LogCaptureFixture) -> None:
    before = obs_config.get_observability_mode()
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        obs_config.apply_explicit_config(mode="bogus", db_path=None, jsonl_dir=None, source="test")
    assert len(_warnings(caplog, "bogus")) == 1
    assert obs_config.get_observability_mode() == before


def test_pathlike_honored_and_non_pathlike_ignored(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        obs_config.apply_explicit_config(mode="dual", db_path=tmp_path / "p.db", jsonl_dir=tmp_path / "pj", source="test")
    assert obs_config.get_db_path() == str(tmp_path / "p.db")
    assert obs_config.get_jsonl_dir() == str(tmp_path / "pj")
    assert not _warnings(caplog, "")

    _reset_all()
    before = obs_config.get_db_path()
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        obs_config.apply_explicit_config(mode=None, db_path=123, jsonl_dir=None, source="test")
    assert len(_warnings(caplog, "db_path")) == 1
    assert obs_config.get_db_path() == before


# 7
def test_create_app_explicit_config_writes_run_started(tmp_path: Path) -> None:
    target = tmp_path / "app_audit"
    _app(GovernanceConfig(observability_mode="file_only", jsonl_dir=str(target)))
    records = _run_started(target)
    assert [r["payload"]["run_type"] for r in records] == ["proxy"]
    assert records[0]["payload"]["meta"]["source"] == "moralstack-server"


def test_create_app_none_config_is_env_driven(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", "file_only")
    _app(None)
    assert obs_config._explicit == {}
    assert len(_run_started(tmp_path / "env_jsonl")) == 1


# 8 (§5.6)
def test_apply_raising_never_breaks_client_or_app(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_kw: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(obs_config, "apply_explicit_config", boom)
    with caplog.at_level(logging.WARNING):
        client = _govern(GovernanceConfig(observability_mode="file_only"))
        assert client._run_id
        response = _app(GovernanceConfig(observability_mode="file_only")).post(
            "/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "Q"}]}
        )
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "guidance"
    assert response.headers["X-Moralstack-Decision"] == "NORMAL_COMPLETE"
    # Two SDK calls (govern() and GovernedClient.__init__) plus create_app.
    failures = [
        r
        for r in caplog.records
        if r.name == "moralstack.sdk.bootstrap" and "applying observability config failed" in r.getMessage()
    ]
    assert len(failures) == 3


def test_unwritable_db_path_never_breaks_client_or_app(tmp_path: Path) -> None:
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    bad = str(blocker / "x.db")  # parent is a regular file: directory creation fails
    config = GovernanceConfig(observability_mode="db_only", db_path=bad)
    assert _govern(config)._run_id
    response = _app(config).post("/v1/chat/completions", json={"model": "m", "messages": [{"role": "user", "content": "Q"}]})
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "guidance"


# 9 (B1)
def test_explicit_db_path_after_live_worker_is_refused(
    env_db: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    first = _govern(GovernanceConfig())
    _governed_call(first._run_id, "req-first")  # worker connection now open on the env DB
    explicit = str(tmp_path / "late.db")
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        second = _govern(GovernanceConfig(observability_mode="db_only", db_path=explicit))
    assert len(_warnings(caplog, "db_path")) == 1
    assert obs_config.get_db_path() == env_db
    assert not Path(explicit).exists()
    _governed_call(second._run_id, "req-second")
    assert _count(env_db, "runs", second._run_id) == 1
    assert _count(env_db, "requests", second._run_id) == 1
    assert _count(env_db, "llm_calls", second._run_id) == 1
    assert not Path(explicit).exists()


# 9a (N1)
def test_second_explicit_db_path_on_unbound_target_is_refused(caplog: pytest.LogCaptureFixture, tmp_path: Path) -> None:
    from moralstack.observability import get_obs

    first_db, second_db = str(tmp_path / "first.db"), str(tmp_path / "second.db")
    first = _govern(GovernanceConfig(observability_mode="db_only", db_path=first_db))
    assert not get_obs().target_bound  # nothing enqueued yet: only the first-call rule can refuse
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        _govern(GovernanceConfig(observability_mode="db_only", db_path=second_db))
    assert len(_warnings(caplog, "target already fixed")) == 1
    assert obs_config.get_db_path() == first_db
    assert not Path(second_db).exists()
    _governed_call(first._run_id, "req-n1")
    assert _count(first_db, "runs", first._run_id) == 1
    assert _count(first_db, "requests", first._run_id) == 1
    assert _count(first_db, "llm_calls", first._run_id) == 1
    assert not Path(second_db).exists()


# 9b
@pytest.mark.parametrize("entry", ["create_app", "govern"])
def test_explicit_db_path_after_bound_target_is_refused(
    entry: str, env_db: str, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    from moralstack.observability import get_obs

    get_obs().emit(make_envelope(EVENT_DEBUG_EVENT, run_id="r", payload={"marker": "x"}))
    get_obs().flush(timeout=10.0)
    assert get_obs().target_bound
    explicit = str(tmp_path / "bound.db")
    config = GovernanceConfig(observability_mode="db_only", db_path=explicit)
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        _app(config) if entry == "create_app" else _govern(config)
    refusals = _warnings(caplog, "target already bound")
    assert len(refusals) == 1
    assert obs_config.get_db_path() == env_db
    assert not Path(explicit).exists()


# 9c (NB1)
def test_explicit_config_applies_after_reports_import_without_enqueue(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    import moralstack.reports  # noqa: F401
    from moralstack.observability import get_obs

    assert not get_obs().target_bound  # singleton exists, nothing enqueued
    explicit = str(tmp_path / "nb1.db")
    with caplog.at_level(logging.WARNING, logger=_CFG_LOGGER):
        client = _govern(GovernanceConfig(observability_mode="db_only", db_path=explicit))
    assert not _warnings(caplog, "")
    assert obs_config.get_db_path() == explicit
    _governed_call(client._run_id, "req-9c")
    assert _count(explicit, "runs", client._run_id) == 1
    assert _count(explicit, "llm_calls", client._run_id) == 1


# 10
@pytest.mark.parametrize(
    "explicit",
    [
        {"db_path": 123, "observability_mode": object(), "jsonl_dir": None},
        {"db_path": "", "observability_mode": "nope", "jsonl_dir": b"bytes"},
        {"db_path": None, "observability_mode": 5, "jsonl_dir": 7.5},
    ],
)
def test_getters_never_raise(explicit: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(obs_config, "_explicit", explicit)
    assert obs_config.get_db_path() is None or isinstance(obs_config.get_db_path(), str)
    assert obs_config.get_observability_mode() in ("db_only", "dual", "file_only")
    assert isinstance(obs_config.get_jsonl_dir(), str)


# 11 (pin)
def test_real_bootstrap_pipeline_leaves_target_unbound(monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.sdk.bootstrap import _bootstrap_pipeline

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    modules = SimpleNamespace(
        policy=MagicMock(),
        risk_estimator=MagicMock(),
        critic=MagicMock(),
        simulator=MagicMock(),
        hindsight=MagicMock(),
        perspectives=MagicMock(),
        constitution_store=MagicMock(),
        prompt_set=None,
    )
    with (
        patch("moralstack.sdk.bootstrap.build_deliberation_modules", return_value=(modules, None)),
        patch("moralstack.runtime.orchestrator.Orchestrator", return_value=MagicMock()),
    ):
        _bootstrap_pipeline(GovernanceConfig(enable_ledger=False))
    instance = service_module._obs_instance
    assert instance is None or not instance.target_bound


# 12
def test_write_queue_target_bound_flag() -> None:
    queue = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    # The worker must not be the one setting the flag: the enqueue itself has to bind the target.
    queue._open_worker_connection = lambda: None  # type: ignore[method-assign]
    try:
        assert queue.target_bound is False
        queue.submit_envelope(make_envelope(EVENT_DEBUG_EVENT, run_id="r", payload={"marker": "x"}))
        assert queue.target_bound is True
    finally:
        queue.shutdown(timeout=5.0)
    other = ObservabilityWriteQueue(batch_max_items=10, batch_max_delay_ms=1)
    try:
        assert other.target_bound is False
        other._open_worker_connection()
        assert other.target_bound is True
    finally:
        other.shutdown(timeout=5.0)

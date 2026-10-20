"""Prompt-set run audit (Phase A, step A4): run meta on DB and file, per-request link, observability safety.

Covers plan section T5: the shape of ``PromptSet.audit_summary()``, the ``runs.meta_json.prompt_config``
record written by every entry point (SDK, proxy, CLI, benchmark, session fallback), the ``run.started.jsonl``
record, the per-request ``prompt_set`` link, and the "observability never breaks the request" checks.

Every DB test uses a ``tmp_path`` database file (never ``:memory:``: each in-memory connection is a separate DB).
"""

from __future__ import annotations

import importlib.util
import io
import json
import logging
import sqlite3
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock, patch

import pytest

from moralstack.models.risk import OperationalRisk, RiskCategory
from moralstack.models.risk.schema import RiskEstimation
from moralstack.observability import router
from moralstack.observability import service as service_module
from moralstack.observability.context import set_current_run_id
from moralstack.observability.conversation_events import emit_request_meta_updated, emit_run_started_file_record
from moralstack.observability.service import get_obs
from moralstack.orchestration.default_persistence import DefaultPersistence
from moralstack.prompts.catalog import (
    ENV_PROMPTS_DIR,
    PromptSet,
    default_prompt_set,
    load_prompt_set,
    prompt_run_meta,
)
from moralstack.sdk.config import GovernanceConfig
from tests.prompt_markers import make_marker_dir
from tests.test_orchestrator import MockCritic, MockPolicyLLM, create_orchestrator

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_moralstack.py"


# --------------------------------------------------------------------------------------
# Fixtures and helpers
# --------------------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Fresh obs singleton, sinks and run.started once-per-run guard around every test."""
    _ARM.clear()
    from moralstack.observability import conversation_events

    def _reset() -> None:
        try:
            get_obs().shutdown(timeout=1.0)
        except Exception:
            pass
        service_module._obs_instance = None
        router._sqlite_sink = None
        router._jsonl_sink = None
        with conversation_events._run_started_lock:
            conversation_events._run_started_written.clear()

    _reset()
    yield
    _reset()


def _configure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str, *, with_db: bool = True) -> tuple[str, Path]:
    """Point the observability env at ``tmp_path``. Returns (db_path or "", jsonl_dir)."""
    jsonl_dir = tmp_path / "jsonl"
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_JSONL_DIR", str(jsonl_dir))
    monkeypatch.setenv("MORALSTACK_OBSERVABILITY_MODE", mode)
    if with_db:
        db = str(tmp_path / "audit.db")
        monkeypatch.setenv("MORALSTACK_OBSERVABILITY_DB_PATH", db)
        monkeypatch.setenv("MORALSTACK_DB_PATH", db)
        return db, jsonl_dir
    monkeypatch.delenv("MORALSTACK_OBSERVABILITY_DB_PATH", raising=False)
    monkeypatch.setenv("MORALSTACK_DB_PATH", "")
    return "", jsonl_dir


def _jsonl(jsonl_dir: Path, event_type: str) -> list[dict[str, Any]]:
    path = jsonl_dir / f"{event_type}.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _run_row(db: str, run_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute("SELECT run_type, meta_json FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {"run_type": row[0], "meta": json.loads(row[1])}


def _request_meta(db: str, run_id: str, request_id: str) -> dict[str, Any] | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT meta_json FROM requests WHERE run_id = ? AND request_id = ?", (run_id, request_id)
        ).fetchone()
    finally:
        conn.close()
    return None if row is None else json.loads(row[0] or "{}")


class _RiskBenign:
    def estimate(self, prompt: str, **kwargs: Any) -> RiskEstimation:
        return RiskEstimation(
            score=0.1,
            confidence=0.9,
            risk_category=RiskCategory.BENIGN,
            operational_risk=OperationalRisk.NONE,
            detected_domain="general",
        )


def _orch(prompt_set: PromptSet | None = None) -> Any:
    return create_orchestrator(
        policy=MockPolicyLLM(), risk_estimator=_RiskBenign(), critic=MockCritic(), prompt_set=prompt_set
    )


def _override_dir(tmp_path: Path) -> Path:
    """A deployer dir: safety-template text (missing placeholder), policy text, and a risk ``when`` override."""
    d = tmp_path / "deployer_prompts"
    d.mkdir()
    (d / "o.yaml").write_text(
        json.dumps(
            {
                "version": 1,
                "fragments": {
                    "critic.full.user": {"text": "{principles} {request} {response} {previous_guidance_section}"},
                    "policy.system": {"text": "override system text"},
                    "risk.intent.step0": {"when": "never"},
                },
            }
        ),
        encoding="utf-8",
    )
    return d


# --------------------------------------------------------------------------------------
# Shape of the audit summary (D2, D7, D9, Q1)
# --------------------------------------------------------------------------------------


def test_default_summary_shape() -> None:
    ps = default_prompt_set()
    s = ps.audit_summary()
    assert s["schema"] == 1
    assert s["is_default"] is True
    assert s["ps"] == ps.ps
    assert s["prompts_dir"] is None
    assert s["override_files"] == []
    assert s["safety_fragments_overridden"] == []
    assert s["set_fingerprint"] == s["default_fingerprint"]
    assert set(s["fragments"]) == set(ps.fragment_ids())
    for entry in s["fragments"].values():
        assert set(entry) == {"sha256", "when", "origin"}
        assert entry["origin"] == "default"
        assert "text" not in entry


def test_override_summary_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _override_dir(tmp_path)
    monkeypatch.chdir(tmp_path)
    ps = load_prompt_set("deployer_prompts")  # relative path must be stored as configured
    s = ps.audit_summary()
    assert s["is_default"] is False
    assert s["prompts_dir"] == "deployer_prompts"
    assert [f["file"] for f in s["override_files"]] == ["o.yaml"]
    assert len(s["override_files"][0]["sha256"]) == 64
    assert s["safety_fragments_overridden"] == ["critic.full.user", "risk.intent.step0"]
    frags = s["fragments"]
    critic = frags["critic.full.user"]
    assert critic["origin"] == "override" and critic["override_file"] == "o.yaml"
    assert critic["overridden"] == ["text"] and "text" in critic
    assert critic["missing_placeholders"] == ["risk_signals"]
    policy = frags["policy.system"]
    assert policy["overridden"] == ["text"] and policy["text"] == "override system text"
    assert "missing_placeholders" not in policy
    when_only = frags["risk.intent.step0"]
    assert when_only["overridden"] == ["when"] and when_only["when"] == "never"
    assert "text" not in when_only and "missing_placeholders" not in when_only
    # only text-overridden fragments carry a text key
    assert {fid for fid, e in frags.items() if "text" in e} == {"critic.full.user", "policy.system"}


def test_summary_hashes_match_live_texts_and_size_bound(tmp_path: Path) -> None:
    from moralstack.prompts.catalog import _sha256_text

    for ps in (default_prompt_set(), load_prompt_set(str(_override_dir(tmp_path)))):
        s = ps.audit_summary()
        for fid, entry in s["fragments"].items():
            assert entry["sha256"] == _sha256_text(ps._fragments[fid].text), fid
        json.dumps(s)  # JSON-serializable
    assert len(json.dumps(default_prompt_set().audit_summary())) < 64 * 1024


# --------------------------------------------------------------------------------------
# DB, per entry point
# --------------------------------------------------------------------------------------


_ARM: list[Callable[[], None]] = []


def _arm() -> None:
    """Entry points call this right before the step that computes the run meta (fault-injection hook)."""
    for hook in _ARM:
        hook()


def _sdk_entry() -> tuple[str, str]:
    from moralstack.sdk.wrapper import GovernedClient

    orch = _orch()
    _arm()
    client = GovernedClient(MagicMock(), orch, GovernanceConfig())
    return client._run_id, "sdk_session"


def _proxy_entry() -> tuple[str, str]:
    from fastapi.testclient import TestClient

    from moralstack.server.proxy import create_app

    orch = _orch()
    _arm()
    app = create_app(openai_client=MagicMock(), orchestrator=orch, config=GovernanceConfig())
    return TestClient(app).get("/healthz").json()["run_id"], "proxy"


def _cli_entry() -> tuple[str, str]:
    from moralstack.cli.run import CLIConfig, MoralStackCLI

    cli = MoralStackCLI(CLIConfig(use_mock=True))
    with redirect_stdout(io.StringIO()):
        assert cli.setup() is True
    _arm()
    return cli._setup_run_context("hello audit"), "single"


class _StopBenchmark(Exception):
    pass


def _load_benchmark(monkeypatch: pytest.MonkeyPatch) -> Any:
    name = "benchmark_moralstack_a4"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, name, mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _bench_entry_factory(monkeypatch: pytest.MonkeyPatch) -> Callable[[], tuple[str, str]]:
    mod = _load_benchmark(monkeypatch)

    def _entry() -> tuple[str, str]:
        runner = mod.BenchmarkRunner(api_key="sk-test-dummy", num_questions=1)

        def _stop(*a: Any, **k: Any) -> None:
            raise _StopBenchmark

        # run() builds the run meta, then asks for the models config: stop right there (no LLM, no spend).
        monkeypatch.setattr(mod, "get_benchmark_models_config", _stop)
        _arm()
        with pytest.raises(_StopBenchmark):
            runner.run()
        return runner._persistence_run_id, "benchmark"

    return _entry


def _session_entry() -> tuple[str, str]:
    run_id = "session-run-fixed"
    set_current_run_id(run_id)
    _arm()
    result = _orch().process("Hello")  # the orchestrator builds the session-fallback meta at construction
    assert result.request_id
    return run_id, "session"


def _entries(monkeypatch: pytest.MonkeyPatch) -> dict[str, Callable[[], tuple[str, str]]]:
    return {
        "sdk": _sdk_entry,
        "proxy": _proxy_entry,
        "cli": _cli_entry,
        "benchmark": _bench_entry_factory(monkeypatch),
        "session": _session_entry,
    }


ENTRY_NAMES = ["sdk", "proxy", "cli", "benchmark", "session"]


@pytest.mark.parametrize("name", ENTRY_NAMES)
def test_db_only_run_meta_has_prompt_config(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, jsonl_dir = _configure(monkeypatch, tmp_path, "db_only")
    run_id, run_type = _entries(monkeypatch)[name]()
    get_obs().flush(timeout=5.0)
    row = _run_row(db, run_id)
    assert row is not None, name
    assert row["run_type"] == run_type
    assert row["meta"]["prompt_config"] == default_prompt_set().audit_summary()
    assert _jsonl(jsonl_dir, "run.started") == []  # db_only writes no JSONL


def test_legacy_meta_keys_are_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    run_id, _t = _proxy_entry()
    assert _run_row(db, run_id)["meta"]["source"] == "moralstack-server"
    run_id, _t = _cli_entry()
    assert _run_row(db, run_id)["meta"]["prompt_preview"] == "hello audit"


def test_sdk_patched_init_run_context_is_called_once_and_create_run_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from moralstack.sdk.wrapper import GovernedClient

    db, _ = _configure(monkeypatch, tmp_path, "dual")
    orch = _orch()
    with (
        patch("moralstack.sdk.wrapper.GovernedClient._init_run_context", wraps=GovernedClient._init_run_context) as init,
        patch("moralstack.observability.sinks.sqlite_sink.create_run", wraps=None) as create,
    ):
        create.return_value = True
        GovernedClient(MagicMock(), orch, GovernanceConfig())
    init.assert_called_once()
    assert init.call_args.args[0] == prompt_run_meta(orch.prompt_set)
    create.assert_called_once()
    assert create.call_args.args[1] == "sdk_session"
    assert create.call_args.args[2]["prompt_config"]["ps"] == orch.prompt_set.ps


def test_proxy_controller_magicmock_and_override_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from moralstack.server.proxy import create_app

    db, _ = _configure(monkeypatch, tmp_path, "db_only")

    def _meta_for(orchestrator: Any) -> dict[str, Any]:
        app = create_app(openai_client=MagicMock(), orchestrator=orchestrator, config=GovernanceConfig())
        rid = TestClient(app).get("/healthz").json()["run_id"]
        return _run_row(db, rid)["meta"]

    facade = _orch()
    assert _meta_for(facade)["prompt_config"]["ps"] == facade.prompt_set.ps
    assert _meta_for(facade._controller)["prompt_config"]["ps"] == facade.prompt_set.ps  # OrchestrationController
    assert _meta_for(MagicMock()) == {"source": "moralstack-server"}  # MagicMock orchestrator: no key
    custom = load_prompt_set(str(_override_dir(tmp_path)))
    meta = _meta_for(_orch(custom))
    assert meta["prompt_config"]["is_default"] is False
    assert meta["prompt_config"]["safety_fragments_overridden"] == ["critic.full.user", "risk.intent.step0"]


def test_proxy_lazy_run_row_reinsert_carries_prompt_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import moralstack.server.proxy as proxy_mod

    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    monkeypatch.setattr(proxy_mod, "_run_row_ensured_for", None)
    meta = {"source": "moralstack-server", **prompt_run_meta(default_prompt_set())}
    proxy_mod._ensure_request_row(
        proxy_run_id="lazy-run", request_id="lazy-req", prompt="q", conversation_id=None, turn_index=0, run_meta=meta
    )
    row = _run_row(db, "lazy-run")
    assert row is not None and row["meta"]["prompt_config"]["ps"] == default_prompt_set().ps
    assert _request_meta(db, "lazy-run", "lazy-req") is not None


def test_cli_setup_run_context_with_and_without_prompt_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.cli.run import CLIConfig, MoralStackCLI

    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    cli = MoralStackCLI(CLIConfig(use_mock=True))
    with redirect_stdout(io.StringIO()):
        cli.setup()
    with_set = _run_row(db, cli._setup_run_context("p1"))["meta"]
    assert "prompt_config" in with_set and with_set["prompt_preview"] == "p1"
    cli.orchestrator = MagicMock()  # mock orchestrator: not a PromptSet -> no key
    assert _run_row(db, cli._setup_run_context("p2"))["meta"] == {"prompt_preview": "p2"}
    cli.orchestrator = None
    assert _run_row(db, cli._setup_run_context("p3"))["meta"] == {"prompt_preview": "p3"}


def test_benchmark_meta_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    run_id, _t = _bench_entry_factory(monkeypatch)()
    meta = _run_row(db, run_id)["meta"]
    assert {"model", "total_questions", "prompt_config"} <= set(meta)
    assert meta["total_questions"] == 1


def test_benchmark_bad_prompts_dir_fails_before_any_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.prompts.catalog import PromptConfigError

    mod = _load_benchmark(monkeypatch)
    calls: list[str] = []
    monkeypatch.setattr(mod, "OpenAIClient", lambda *a, **k: calls.append("OpenAIClient"))
    monkeypatch.setattr(mod, "MoralStackRunner", lambda *a, **k: calls.append("MoralStackRunner"))
    empty = tmp_path / "empty_prompts"
    empty.mkdir()
    monkeypatch.setenv(ENV_PROMPTS_DIR, str(empty))
    with pytest.raises(PromptConfigError):
        mod.BenchmarkRunner(api_key="sk-test-dummy")
    assert calls == []


def test_session_fallback_keeps_insert_or_ignore_first_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability.sinks.sqlite_sink import create_run, init_db

    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    init_db(db)
    meta = prompt_run_meta(default_prompt_set())
    set_current_run_id("fallback-new")
    DefaultPersistence(run_meta=meta).ensure_run_and_upsert_request("req-new", "p")
    assert _run_row(db, "fallback-new")["meta"] == meta
    create_run("fallback-old", "sdk_session", {"first": True})
    set_current_run_id("fallback-old")
    DefaultPersistence(run_meta=meta).ensure_run_and_upsert_request("req-old", "p")
    row = _run_row(db, "fallback-old")
    assert row["run_type"] == "sdk_session" and row["meta"] == {"first": True}


def test_orchestrator_prompt_set_is_read_only() -> None:
    orch = _orch()
    assert orch.prompt_set is default_prompt_set()
    with pytest.raises(AttributeError):
        orch.prompt_set = load_prompt_set(None)  # type: ignore[misc]


# --------------------------------------------------------------------------------------
# File channel (run.started.jsonl)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["file_only", "dual"])
@pytest.mark.parametrize("name", ["sdk", "proxy", "cli", "benchmark"])
def test_file_channel_one_run_started_line(name: str, mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, mode, with_db=(mode == "dual"))
    run_id, run_type = _entries(monkeypatch)[name]()
    assert run_id
    lines = _jsonl(jsonl_dir, "run.started")
    assert len(lines) == 1, lines
    env = lines[0]
    assert env["event_type"] == "run.started" and env["run_id"] == run_id
    assert env["payload"]["run_type"] == run_type
    assert env["payload"]["meta"]["prompt_config"] == default_prompt_set().audit_summary()


def test_dual_calls_create_run_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability.sinks import sqlite_sink

    _configure(monkeypatch, tmp_path, "dual")
    real = sqlite_sink.create_run
    with patch("moralstack.observability.sinks.sqlite_sink.create_run", side_effect=real) as create:
        _sdk_entry()
    assert create.call_count == 1


def test_run_started_guard_is_first_writer_and_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability import conversation_events

    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    emit_run_started_file_record("r1", "sdk_session", {"a": 1})
    emit_run_started_file_record("r1", "session", {"b": 2})
    lines = _jsonl(jsonl_dir, "run.started")
    assert len(lines) == 1 and lines[0]["payload"]["run_type"] == "sdk_session"
    for i in range(conversation_events._RUN_STARTED_CAPACITY + 10):
        conversation_events._claim_run_started(f"fill-{i}")
    assert len(conversation_events._run_started_written) == conversation_events._RUN_STARTED_CAPACITY
    assert "r1" not in conversation_events._run_started_written  # oldest evicted first
    newest = f"fill-{conversation_events._RUN_STARTED_CAPACITY + 9}"
    assert newest in conversation_events._run_started_written
    emit_run_started_file_record("", "x", {})  # empty run id: no-op
    emit_run_started_file_record(None, "x", {})  # type: ignore[arg-type]


def test_cli_and_benchmark_set_a_run_id_in_file_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    for entry in (_cli_entry, _bench_entry_factory(monkeypatch)):
        from moralstack.observability.context import get_current_run_id

        run_id, _t = entry()
        assert run_id
        assert get_current_run_id() == run_id


# --------------------------------------------------------------------------------------
# Session fallback file record (Q3)
# --------------------------------------------------------------------------------------


def test_session_fallback_file_only_writes_one_session_line(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    run_id, _t = _session_entry()
    lines = _jsonl(jsonl_dir, "run.started")
    assert len(lines) == 1
    assert lines[0]["run_id"] == run_id and lines[0]["payload"]["run_type"] == "session"
    assert lines[0]["payload"]["meta"]["prompt_config"]["ps"] == default_prompt_set().ps
    set_current_run_id(run_id)
    _orch().process("Second request, same run")  # same run id: no second line
    assert len(_jsonl(jsonl_dir, "run.started")) == 1


def test_session_fallback_does_not_duplicate_entry_point_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability.sinks import sqlite_sink
    from moralstack.sdk.wrapper import GovernedClient

    db, jsonl_dir = _configure(monkeypatch, tmp_path, "dual")
    orch = _orch()
    real = sqlite_sink.create_run
    with patch("moralstack.observability.sinks.sqlite_sink.create_run", side_effect=real) as entry_create:
        client = GovernedClient(MagicMock(), orch, GovernanceConfig())
    assert entry_create.call_count == 1
    set_current_run_id(client._run_id)
    orch.process("Hello")
    lines = _jsonl(jsonl_dir, "run.started")
    assert len(lines) == 1 and lines[0]["payload"]["run_type"] == "sdk_session"
    assert _run_row(db, client._run_id)["run_type"] == "sdk_session"


def test_session_fallback_helper_raising_does_not_break_process(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, tmp_path, "dual")
    set_current_run_id("raising-helper-run")
    with patch(
        "moralstack.orchestration.default_persistence.emit_run_started_file_record", side_effect=RuntimeError("boom")
    ):
        result = _orch().process("Hello")
    assert result.request_id and result.response.content


# --------------------------------------------------------------------------------------
# CLI in file_only (MT7)
# --------------------------------------------------------------------------------------


def test_cli_file_only_prompt_run_writes_llm_calls_and_run_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from moralstack.cli.run import CLIConfig, MoralStackCLI
    from moralstack.observability.context import get_current_run_id
    from moralstack.observability.sinks import sqlite_sink

    monkeypatch.setenv("MORALSTACK_ORCHESTRATOR_TIMEOUT_MS", "10000")
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    cli = MoralStackCLI(CLIConfig(use_mock=True))
    with redirect_stdout(io.StringIO()):
        cli.setup()
    with caplog.at_level(logging.WARNING), redirect_stdout(io.StringIO()):
        cli._process_prompt("What is 2+2?")
    get_obs().flush(timeout=5.0)
    run_id = get_current_run_id()
    assert run_id
    assert sqlite_sink.end_run(run_id) is False  # file_only: no DB, no raise
    assert len(_jsonl(jsonl_dir, "run.started")) == 1
    assert _jsonl(jsonl_dir, "llm.call"), "the mock policy run must write llm.call.jsonl"
    assert [r for r in caplog.records if r.name.startswith("moralstack.observability")] == []


# --------------------------------------------------------------------------------------
# Per-request link
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["db_only", "dual"])
def test_request_link_in_db_and_file(mode: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util as _u

    db, jsonl_dir = _configure(monkeypatch, tmp_path, mode)
    set_current_run_id("link-run")
    orch = _orch()
    result = orch.process("Hello")
    get_obs().flush(timeout=5.0)
    link = {"ps": orch.prompt_set.ps, "is_default": True}
    assert _request_meta(db, "link-run", result.request_id)["prompt_set"] == link
    # link coexists with a later merge (finalization-style keys)
    emit_request_meta_updated(run_id="link-run", request_id=result.request_id, meta={"final_action": "NORMAL_COMPLETE"})
    get_obs().flush(timeout=5.0)
    merged = _request_meta(db, "link-run", result.request_id)
    assert merged["prompt_set"] == link and merged["final_action"] == "NORMAL_COMPLETE"
    # the real finalization owner merges (merge=True) and must not drop the link
    from moralstack.observability.conversation_events import finalize_audit_sync

    finalize_audit_sync(
        run_id="link-run",
        request_id=result.request_id,
        final_action="SAFE_COMPLETE",
        final_response="r",
        domain=None,
        proxy_summary=None,
    )
    final = _request_meta(db, "link-run", result.request_id)
    assert final["prompt_set"] == link and final["final_action"] == "SAFE_COMPLETE"
    if mode == "dual":
        deltas = [e for e in _jsonl(jsonl_dir, "request.meta_updated") if e["request_id"] == result.request_id]
        assert any(e["payload"]["meta"].get("prompt_set") == link for e in deltas)
        spec = _u.spec_from_file_location(
            "consolidate_jsonl_meta_a4", Path(__file__).resolve().parent.parent / "scripts" / "consolidate_jsonl_meta.py"
        )
        mod = _u.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        envelopes = mod._load_envelopes([jsonl_dir / "request.meta_updated.jsonl"])
        assert mod.consolidate(envelopes)[f"link-run:{result.request_id}"]["prompt_set"] == link


def test_request_link_file_only_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    set_current_run_id("link-file-run")
    result = _orch().process("Hello")
    get_obs().flush(timeout=5.0)
    deltas = [e for e in _jsonl(jsonl_dir, "request.meta_updated") if e["request_id"] == result.request_id]
    assert deltas and deltas[0]["payload"]["meta"]["prompt_set"]["is_default"] is True


def test_request_link_without_run_id_is_a_noop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, jsonl_dir = _configure(monkeypatch, tmp_path, "dual")
    set_current_run_id("")  # falsy: no run
    result = _orch().process("Hello")
    get_obs().flush(timeout=5.0)
    assert result.request_id
    assert _jsonl(jsonl_dir, "request.meta_updated") == []


def test_request_link_emit_raising_does_not_change_the_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, tmp_path, "dual")
    set_current_run_id("link-raise-1")
    baseline = _orch().process("Hello")
    set_current_run_id("link-raise-2")
    with patch("moralstack.observability.conversation_events.emit_request_meta_updated", side_effect=RuntimeError("boom")):
        result = _orch().process("Hello")
    assert result.response.metadata.final_action == baseline.response.metadata.final_action
    assert result.response.content == baseline.response.content


def test_request_link_missing_row_is_a_counted_failure_not_an_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    from moralstack.observability.sinks.sqlite_sink import init_db

    init_db(db)
    emit_request_meta_updated(run_id="ghost-run", request_id="ghost-req", meta={"prompt_set": {"ps": "x"}})
    get_obs().flush(timeout=5.0)  # must not raise
    assert _request_meta(db, "ghost-run", "ghost-req") is None
    stats = get_obs().stats()
    assert stats["failed_count"] + stats["sqlite_failed_count"] >= 1, stats


# --------------------------------------------------------------------------------------
# Observability never breaks the request (5.6)
# --------------------------------------------------------------------------------------

_LEGACY_META = {
    "sdk": {},
    "proxy": {"source": "moralstack-server"},
    "cli": {"prompt_preview": "hello audit"},
    "benchmark": {"model": "gpt-4o", "total_questions": 1},
    "session": {},
}


@pytest.mark.parametrize("name", ENTRY_NAMES)
def test_audit_summary_raising_creates_run_with_legacy_meta(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "db_only")

    def _boom(self: PromptSet) -> dict[str, Any]:
        raise RuntimeError("summary exploded")

    entries = _entries(monkeypatch)
    _ARM.append(lambda: monkeypatch.setattr(PromptSet, "audit_summary", _boom))
    with caplog.at_level(logging.DEBUG, logger="moralstack.prompts.catalog"):
        run_id, _t = entries[name]()
    row = _run_row(db, run_id)
    assert row is not None, name
    assert row["meta"] == _LEGACY_META[name]
    failures = [
        r for r in caplog.records if r.name == "moralstack.prompts.catalog" and "prompt_run_meta failed" in r.message
    ]
    assert len(failures) == 1 and failures[0].levelno == logging.DEBUG


def test_json_dumps_failure_in_meta_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    _ARM.append(lambda: monkeypatch.setattr(PromptSet, "audit_summary", lambda self: {"bad": object()}))
    with caplog.at_level(logging.DEBUG, logger="moralstack.prompts.catalog"):
        run_id, _t = _sdk_entry()
    assert _run_row(db, run_id)["meta"] == {}  # run row exists with the legacy meta
    failures = [
        r for r in caplog.records if r.name == "moralstack.prompts.catalog" and "prompt_run_meta failed" in r.message
    ]
    assert len(failures) == 1 and failures[0].levelno == logging.DEBUG
    assert prompt_run_meta(default_prompt_set()) == {}


def test_create_run_failure_does_not_break_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from moralstack.server.proxy import create_app

    _configure(monkeypatch, tmp_path, "db_only")
    orch = MagicMock()
    with patch("moralstack.observability.sinks.sqlite_sink.create_run", side_effect=RuntimeError("db down")):
        app = create_app(openai_client=MagicMock(), orchestrator=orch, config=GovernanceConfig())
    assert TestClient(app).get("/healthz").status_code == 200


def test_file_only_mode_creates_no_row_and_logs_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from fastapi.testclient import TestClient

    from moralstack.server.proxy import create_app

    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    with caplog.at_level(logging.INFO, logger="moralstack.server.proxy"):
        app = create_app(openai_client=MagicMock(), orchestrator=_orch(), config=GovernanceConfig())
    rid = TestClient(app).get("/healthz").json()["run_id"]
    assert rid
    assert not (tmp_path / "audit.db").exists()  # no DB row, no DB file
    assert any("observability run initialized" in r.message and r.levelno == logging.INFO for r in caplog.records)
    assert len(_jsonl(jsonl_dir, "run.started")) == 1


def test_db_only_without_db_path_creates_nothing_and_logs_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from fastapi.testclient import TestClient

    from moralstack.server.proxy import create_app

    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "db_only", with_db=False)
    with caplog.at_level(logging.INFO, logger="moralstack.server.proxy"):
        app = create_app(openai_client=MagicMock(), orchestrator=_orch(), config=GovernanceConfig())
    assert TestClient(app).get("/healthz").json()["run_id"] == ""
    assert any("observability not configured" in r.message for r in caplog.records)
    assert _jsonl(jsonl_dir, "run.started") == []


def test_route_jsonl_raising_does_not_break_run_creation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db, _ = _configure(monkeypatch, tmp_path, "dual")
    sink = MagicMock()
    sink.write_envelope.side_effect = RuntimeError("disk full")
    monkeypatch.setattr(router, "_jsonl_sink", sink)
    run_id, _t = _sdk_entry()
    assert _run_row(db, run_id) is not None
    with patch("moralstack.observability.router._get_jsonl_sink", side_effect=RuntimeError("no sink")):
        emit_run_started_file_record("route-boom", "x", {})  # never raises
        router.route_jsonl(MagicMock())  # never raises


def test_route_jsonl_is_jsonl_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability.events import EVENT_RUN_STARTED, make_envelope

    env = make_envelope(EVENT_RUN_STARTED, run_id="rj", payload={"run_type": "x", "meta": {}})
    db, jsonl_dir = _configure(monkeypatch, tmp_path, "db_only")
    router.route_jsonl(env)
    assert _jsonl(jsonl_dir, "run.started") == []
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "dual")
    router.route_jsonl(env)
    assert len(_jsonl(jsonl_dir, "run.started")) == 1
    assert not Path(db).exists()  # the SQLite sink was never touched


def test_file_only_without_run_id_does_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    set_current_run_id("")
    result = _orch().process("Hello")
    get_obs().flush(timeout=5.0)
    assert result.request_id
    assert _jsonl(jsonl_dir, "run.started") == []
    assert _jsonl(jsonl_dir, "request.meta_updated") == []


# --------------------------------------------------------------------------------------
# Readers / UI tolerance
# --------------------------------------------------------------------------------------


def test_readers_list_runs_carrying_a_large_prompt_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from moralstack.observability.read_store import SqliteReadStore
    from moralstack.observability.sinks.sqlite_sink import create_run, init_db

    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    init_db(db)
    meta = prompt_run_meta(load_prompt_set(str(make_marker_dir(tmp_path))))
    assert len(json.dumps(meta)) > 20_000
    create_run("big-run", "sdk_session", meta)
    runs = SqliteReadStore().get_all_runs()
    assert "big-run" in [r["run_id"] for r in runs]


def test_ui_request_page_renders_with_prompt_set_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from moralstack.observability.sinks.sqlite_sink import create_run, init_db, update_request_meta, upsert_request

    db, _ = _configure(monkeypatch, tmp_path, "db_only")
    monkeypatch.setattr("moralstack.observability.sinks.sqlite_sink.get_db_path", lambda: db)
    monkeypatch.setattr("moralstack.observability.read_store.get_db_path", lambda: db)
    init_db(db)
    create_run("ui-run", "sdk_session", prompt_run_meta(default_prompt_set()))
    upsert_request("ui-run", "ui-req", prompt="hi", domain="cyber")
    update_request_meta(
        "ui-run", "ui-req", {"final_action": "NORMAL_COMPLETE", "prompt_set": default_prompt_set().request_link()}
    )
    from moralstack.ui import app as app_module

    monkeypatch.setenv("MORALSTACK_UI_USERNAME", "admin")
    monkeypatch.setenv("MORALSTACK_UI_PASSWORD", "test")
    monkeypatch.setattr(app_module, "_UI_USERNAME", "admin")
    monkeypatch.setattr(app_module, "_UI_PASSWORD", "test")
    client = TestClient(app_module.create_app(), follow_redirects=False)
    assert client.post("/login", data={"username": "admin", "password": "test"}).status_code in (200, 303)
    assert client.get("/runs/ui-run/requests/ui-req").status_code == 200
    assert client.get("/runs").status_code == 200


# --------------------------------------------------------------------------------------
# Run id without a DB: only in file_only (D-A4-2)
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["cli", "benchmark"])
def test_dual_without_db_sets_no_run_id_and_writes_nothing(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "dual", with_db=False)
    run_id, _t = _entries(monkeypatch)[name]()
    assert not run_id
    assert _jsonl(jsonl_dir, "run.started") == []


@pytest.mark.parametrize("name", ["cli", "benchmark"])
def test_file_only_without_db_sets_a_run_id(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _db, jsonl_dir = _configure(monkeypatch, tmp_path, "file_only", with_db=False)
    run_id, _t = _entries(monkeypatch)[name]()
    assert run_id
    assert len(_jsonl(jsonl_dir, "run.started")) == 1

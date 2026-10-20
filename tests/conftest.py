"""
Pytest configuration for MoralStack tests.

Provides session-scoped fixtures to speed up tests (in-memory DB, etc.) and,
critically, isolates every test run from the developer's real `.env` so the
suite never writes to the configured `moralstack.db` / `logs/observability`.
"""

from __future__ import annotations

import os

import pytest

# --- Hermetic `.env` isolation (runs at conftest import, before test modules) ---
#
# Two code paths load the project `.env` with `override=True`, which would
# re-inject MORALSTACK_OBSERVABILITY_DB_PATH=moralstack.db (+ MODE=dual) into the
# environment mid-session and redirect all test persistence to the developer's
# real DB and JSONL logs:
#   * moralstack.utils.env_loader.load_env() — called by sdk bootstrap/wrapper/cli
#     (resolves `dotenv.load_dotenv` at call time);
#   * moralstack.ui.app — loads `.env` at *import* time via a top-level
#     `from dotenv import load_dotenv`.
# Neutering `dotenv.load_dotenv` here — at conftest import, before any test
# module (incl. `moralstack.ui.app`) is imported — closes both vectors
# regardless of import timing. Tests are offline/mocked and must never depend on
# the developer's `.env`.
try:
    import dotenv

    def _no_dotenv(*_args: object, **_kwargs: object) -> bool:
        """No-op stand-in for dotenv.load_dotenv during the test session."""
        return False

    dotenv.load_dotenv = _no_dotenv  # type: ignore[assignment]
except ImportError:
    pass

# --- Shared default SSL context (test-suite speed only) ---
#
# Every `openai.OpenAI(...)` builds an httpx transport, which calls
# `httpx._config.create_ssl_context()` and re-parses the whole certifi CA bundle
# (`SSLContext.load_verify_locations`). With OpenSSL 3.x on Windows that costs
# ~0.5 s of CPU per client, and hundreds of tests construct clients: it was the
# dominant cost of the suite and serialized badly under pytest-xdist. Tests are
# offline/mocked (no TLS handshake ever happens), so the default-verify context is
# built once per process and reused. Non-default arguments (custom `verify`, a
# client `cert`, SSL_CERT_FILE/SSL_CERT_DIR) fall through to the original function.
try:
    import httpx._transports.default as _httpx_default_transport

    _original_create_ssl_context = _httpx_default_transport.create_ssl_context
    _ssl_context_cache: dict[tuple[bool, str | None, str | None], object] = {}

    def _cached_create_ssl_context(verify=True, cert=None, trust_env=True):  # type: ignore[no-untyped-def]
        if verify is not True or cert is not None:
            return _original_create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
        key = (bool(trust_env), os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR"))
        ctx = _ssl_context_cache.get(key)
        if ctx is None:
            ctx = _original_create_ssl_context(verify=verify, cert=cert, trust_env=trust_env)
            _ssl_context_cache[key] = ctx
        return ctx

    _httpx_default_transport.create_ssl_context = _cached_create_ssl_context  # type: ignore[assignment]
except ImportError:
    pass

# --- Shared compiled-template cache for the UI (test-suite speed only) ---
#
# `moralstack.ui.app.create_app()` builds a fresh `Jinja2Templates` (hence a fresh
# `jinja2.Environment`) on every call, and UI tests create one app per test, so
# every test re-lexes/re-compiles the same templates (~15 s of CPU per serial run).
# A process-wide bytecode cache, attached only to the environments starlette
# builds, lets each template compile once per process. Correctness guards: the
# entry is keyed on template name/filename plus the compile-relevant environment
# settings, and it is reused only when the template source checksum matches, so an
# edited template is recompiled.
try:
    import jinja2
    from jinja2.bccache import Bucket
    from starlette.templating import Jinja2Templates

    class _SharedJinjaBytecodeCache(jinja2.BytecodeCache):
        def __init__(self) -> None:
            self._store: dict[str, tuple[str, object]] = {}

        def get_bucket(self, environment, name, filename, source):  # type: ignore[no-untyped-def]
            autoescape = environment.autoescape
            if callable(autoescape):
                autoescape = autoescape(name)
            fingerprint = repr(
                (
                    bool(autoescape),
                    environment.optimized,
                    sorted(environment.extensions),
                    environment.block_start_string,
                    environment.block_end_string,
                    environment.variable_start_string,
                    environment.variable_end_string,
                    environment.comment_start_string,
                    environment.comment_end_string,
                    environment.line_statement_prefix,
                    environment.line_comment_prefix,
                    environment.trim_blocks,
                    environment.lstrip_blocks,
                    environment.newline_sequence,
                    environment.keep_trailing_newline,
                )
            )
            key = self.get_cache_key(name, filename) + "|" + fingerprint
            bucket = Bucket(environment, key, self.get_source_checksum(source))
            self.load_bytecode(bucket)
            return bucket

        def load_bytecode(self, bucket: Bucket) -> None:
            entry = self._store.get(bucket.key)
            if entry is not None and entry[0] == bucket.checksum:
                bucket.code = entry[1]  # type: ignore[assignment]

        def dump_bytecode(self, bucket: Bucket) -> None:
            if bucket.code is not None:
                self._store[bucket.key] = (bucket.checksum, bucket.code)

    _shared_jinja_bytecode_cache = _SharedJinjaBytecodeCache()
    _original_setup_env_defaults = Jinja2Templates._setup_env_defaults

    def _setup_env_defaults_with_shared_cache(self, env):  # type: ignore[no-untyped-def]
        if env.bytecode_cache is None:
            env.bytecode_cache = _shared_jinja_bytecode_cache
        _original_setup_env_defaults(self, env)

    Jinja2Templates._setup_env_defaults = _setup_env_defaults_with_shared_cache  # type: ignore[method-assign]
except ImportError:
    pass

# --- No fsync on throwaway test databases (test-suite speed only) ---
#
# Production connections keep SQLite's default `synchronous=FULL`, which fsyncs
# the WAL on every commit. Under an on-access scanner each fsync costs tens of ms
# and serializes across xdist workers; it was the largest remaining cost of the
# suite. Every database a test opens is throwaway (`:memory:` or `tmp_path`), and
# `synchronous` only governs durability across an OS crash/power loss — never
# what a live process reads back — so new connections start at `OFF`. Code under
# test that sets its own level afterwards (e.g. the write-queue worker's NORMAL)
# still wins. Tests that assert the production default opt out with
# `@pytest.mark.sqlite_default_synchronous` (see `_sqlite_synchronous_policy`).
import sqlite3  # noqa: E402

_original_sqlite3_connect = sqlite3.connect
_relax_sqlite_synchronous = True


def _sqlite3_connect_without_fsync(*args, **kwargs):  # type: ignore[no-untyped-def]
    conn = _original_sqlite3_connect(*args, **kwargs)
    if _relax_sqlite_synchronous and not conn.in_transaction:
        conn.execute("PRAGMA synchronous=OFF")
    return conn


sqlite3.connect = _sqlite3_connect_without_fsync  # type: ignore[assignment]


@pytest.fixture(autouse=True)
def _sqlite_synchronous_policy(request):
    """Restore SQLite's default fsync policy for tests marked
    ``sqlite_default_synchronous`` (they assert the production default)."""
    global _relax_sqlite_synchronous
    if request.node.get_closest_marker("sqlite_default_synchronous") is None:
        yield
        return
    _relax_sqlite_synchronous = False
    try:
        yield
    finally:
        _relax_sqlite_synchronous = True


@pytest.fixture(scope="session", autouse=True)
def use_in_memory_db(tmp_path_factory):
    """
    Force throwaway persistence targets for the whole test session:
      * in-memory SQLite (no disk I/O, discarded at process exit);
      * MORALSTACK_OBSERVABILITY_DB_PATH cleared so the file path cannot take
        precedence over the in-memory DB;
      * MORALSTACK_OBSERVABILITY_MODE cleared so the default (db_only for the
        in-memory DB) applies instead of a `.env`-provided `dual`/`file_only`;
      * JSONL output redirected to a session temp dir, so a test that does opt
        into file output never writes into the repo `logs/observability`.
    Individual tests may still override any of these via `monkeypatch`.
    Prior values are restored on teardown.
    """
    obs_jsonl_dir = str(tmp_path_factory.mktemp("obs_jsonl"))
    overrides: dict[str, str | None] = {
        "MORALSTACK_DB_PATH": ":memory:",
        "MORALSTACK_OBSERVABILITY_DB_PATH": None,
        "MORALSTACK_OBSERVABILITY_MODE": None,
        "MORALSTACK_OBSERVABILITY_JSONL_DIR": obs_jsonl_dir,
    }
    saved = {key: os.environ.get(key) for key in overrides}
    for key, value in overrides.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


@pytest.fixture(autouse=True)
def _no_prompts_dir_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer-set MORALSTACK_PROMPTS_DIR must never leak into the suite.

    Tests run on the packaged prompt defaults; a test that wants an override sets the
    variable (or passes ``prompts_dir``) itself.
    """
    monkeypatch.delenv("MORALSTACK_PROMPTS_DIR", raising=False)

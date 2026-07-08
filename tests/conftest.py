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

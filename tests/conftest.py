"""
Pytest configuration for MoralStack tests.

Provides session-scoped fixtures to speed up tests (in-memory DB, etc.).
"""

from __future__ import annotations

import os

import pytest


@pytest.fixture(scope="session", autouse=True)
def use_in_memory_db():
    """
    Use in-memory SQLite for tests to avoid disk I/O.
    Overrides MORALSTACK_DB_PATH for the test session and clears
    MORALSTACK_OBSERVABILITY_DB_PATH to avoid precedence conflicts.
    """
    old = os.environ.get("MORALSTACK_DB_PATH")
    old_obs = os.environ.get("MORALSTACK_OBSERVABILITY_DB_PATH")
    os.environ["MORALSTACK_DB_PATH"] = ":memory:"
    os.environ.pop("MORALSTACK_OBSERVABILITY_DB_PATH", None)
    yield
    if old is not None:
        os.environ["MORALSTACK_DB_PATH"] = old
    else:
        os.environ.pop("MORALSTACK_DB_PATH", None)
    if old_obs is not None:
        os.environ["MORALSTACK_OBSERVABILITY_DB_PATH"] = old_obs
    else:
        os.environ.pop("MORALSTACK_OBSERVABILITY_DB_PATH", None)

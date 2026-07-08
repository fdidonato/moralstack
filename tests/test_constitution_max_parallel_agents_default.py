"""Effective max_parallel_agents default is 4 across all runtime layers.

Intervention 2 (ai/plans/optimize-domain-prefilter-caching-and-parallelism.md):
bumping ONLY ConstitutionRetrieverConfig.max_parallel_agents would be inert on
store-mediated/SDK/CLI paths (the store and the env-fallback resolver override
it). These tests lock the default at every effective source:
ConstitutionRetrieverConfig, ConstitutionStoreConfig, ConstitutionStore.__init__
kwarg, CLIConfig, and resolve_constitution_max_parallel_agents (env fallback +
explicit-still-wins). The MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS env
override must keep winning when set.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from moralstack.cli.models import CLIConfig
from moralstack.cli.shell import parse_args
from moralstack.constitution.retriever import ConstitutionRetrieverConfig
from moralstack.constitution.store import ConstitutionStore, ConstitutionStoreConfig
from moralstack.pipeline.deliberation_stack import (
    ENV_CONSTITUTION_MAX_PARALLEL_AGENTS,
    resolve_constitution_max_parallel_agents,
)

_CONFIG_CORE = Path(__file__).resolve().parent.parent / "moralstack" / "constitution" / "data" / "core.yaml"


@pytest.fixture(autouse=True)
def _env_unset(monkeypatch):
    """Dev/CI .env may set the override; the default-value assertions need it unset."""
    monkeypatch.delenv(ENV_CONSTITUTION_MAX_PARALLEL_AGENTS, raising=False)


def test_constitution_retriever_config_default_is_4():
    assert ConstitutionRetrieverConfig().max_parallel_agents == 4


def test_constitution_store_config_default_is_4():
    assert ConstitutionStoreConfig().max_parallel_agents == 4


@pytest.mark.skipif(not _CONFIG_CORE.exists(), reason="moralstack/constitution/data/core.yaml not present")
def test_constitution_store_init_kwarg_default_is_4():
    store = ConstitutionStore(config_dir=_CONFIG_CORE.parent)
    assert store.max_parallel_agents == 4
    assert store._retriever._config.max_parallel_agents == 4


def test_cli_config_default_is_4():
    assert CLIConfig().max_parallel_agents == 4


def test_resolve_default_is_4_when_env_unset():
    assert resolve_constitution_max_parallel_agents(None) == 4


def test_resolve_env_override_still_wins(monkeypatch):
    monkeypatch.setenv(ENV_CONSTITUTION_MAX_PARALLEL_AGENTS, "2")
    assert resolve_constitution_max_parallel_agents(None) == 2


def test_resolve_explicit_still_wins_over_env(monkeypatch):
    monkeypatch.setenv(ENV_CONSTITUTION_MAX_PARALLEL_AGENTS, "2")
    assert resolve_constitution_max_parallel_agents(7) == 7


def test_cli_parse_args_omitted_flag_resolves_to_4_when_env_unset():
    """shell.py:1158 resolves through resolve_constitution_max_parallel_agents
    when --max-parallel-agents is omitted (argparse default stays None)."""
    with patch.object(sys, "argv", ["mstack_run.py"]):
        config = parse_args()
    assert config.max_parallel_agents == 4


def test_max_parallel_agents_help_text_reads_or_4():
    """shell.py:1123 help string user-facing default reference (Codex review
    6th default source); argparse default itself stays None (shell.py:1122)."""
    buf = io.StringIO()
    with patch.object(sys, "argv", ["mstack_run.py", "--help"]), contextlib.redirect_stdout(buf), pytest.raises(SystemExit):
        parse_args()
    assert "MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS or 4)" in buf.getvalue()

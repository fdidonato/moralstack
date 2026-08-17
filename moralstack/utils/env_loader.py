"""
Load environment variables from .env file in project root.

Searches upward from the current working directory for a .env file
(or pyproject.toml to locate project root), then loads variables.
Call this at application startup before any module reads os.environ.

After loading, empty-valued variables are removed from os.environ so that
third-party libraries (e.g. openai) that read env vars directly do not
interpret "" as a custom (and invalid) value.
"""

from __future__ import annotations

import os
from pathlib import Path

_OPTIONAL_ENV_VARS = frozenset(
    {
        "OPENAI_BASE_URL",
        "OPENAI_TIMEOUT_MS",
        "OPENAI_MAX_RETRIES",
        "OPENAI_TEMPERATURE",
        "OPENAI_TOP_P",
        "MORALSTACK_DECISION_TRACE_PATH",
        "MORALSTACK_VERBOSE",
        "MORALSTACK_CONSTITUTION_MAX_PARALLEL_AGENTS",
        # Risk Estimator (optional overrides)
        "MORALSTACK_RISK_MODEL",
        "MORALSTACK_RISK_INTENT_MODEL",
        "MORALSTACK_RISK_SIGNALS_MODEL",
        "MORALSTACK_RISK_OPERATIONAL_MODEL",
        "MORALSTACK_RISK_LOW_THRESHOLD",
        "MORALSTACK_RISK_MEDIUM_THRESHOLD",
        "MORALSTACK_RISK_MAX_RETRIES",
        "MORALSTACK_RISK_MAX_TOKENS",
        "MORALSTACK_RISK_TEMPERATURE",
        "MORALSTACK_RISK_FALLBACK_SCORE",
        "MORALSTACK_RISK_FALLBACK_CONFIDENCE",
        "MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK",
        "MORALSTACK_RISK_TOP_P",
        "MORALSTACK_RISK_TOP_K",
        "MORALSTACK_RISK_RULE_PREVIEW_LEN",
        "MORALSTACK_RISK_CRISIS_CLAMP_LOW",
        "MORALSTACK_RISK_CRISIS_CLAMP_HIGH",
        "MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD",
        "MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD",
        "MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD",
        # Critic (optional overrides)
        "MORALSTACK_CRITIC_MODEL",
        "MORALSTACK_CRITIC_MAX_RETRIES",
        "MORALSTACK_CRITIC_MAX_TOKENS",
        "MORALSTACK_CRITIC_TEMPERATURE",
        "MORALSTACK_CRITIC_TOP_P",
        "MORALSTACK_CRITIC_TOP_K_PRINCIPLES",
        "MORALSTACK_CRITIC_INCLUDE_EXAMPLES",
        "MORALSTACK_CRITIC_MAX_RULE_LEN",
        # Perspective (optional overrides)
        "MORALSTACK_PERSPECTIVES_MODEL",
        "MORALSTACK_PERSPECTIVES_MAX_RETRIES",
        "MORALSTACK_PERSPECTIVES_MAX_TOKENS",
        "MORALSTACK_PERSPECTIVES_TEMPERATURE",
        "MORALSTACK_PERSPECTIVES_TOP_P",
        "MORALSTACK_PERSPECTIVES_PARALLEL_EVALUATION",
        "MORALSTACK_PERSPECTIVES_MAX_WORKERS",
        "MORALSTACK_PERSPECTIVES_TIMEOUT_SECONDS",
        "MORALSTACK_PERSPECTIVES_MAX_PERSPECTIVES",
        "MORALSTACK_PERSPECTIVES_CONSERVATIVE_ON_FAILURE",
        "MORALSTACK_PERSPECTIVES_ENABLE_CACHING",
        # Simulator (optional overrides)
        "MORALSTACK_SIMULATOR_MODEL",
        "MORALSTACK_SIMULATOR_MAX_RETRIES",
        "MORALSTACK_SIMULATOR_MAX_TOKENS",
        "MORALSTACK_SIMULATOR_TEMPERATURE",
        "MORALSTACK_SIMULATOR_TOP_P",
        "MORALSTACK_SIMULATOR_DEFAULT_NUM_SCENARIOS",
        "MORALSTACK_SIMULATOR_USE_SEEDED_GENERATION",
        "MORALSTACK_SIMULATOR_ENABLE_CACHING",
        # Hindsight (optional overrides)
        "MORALSTACK_HINDSIGHT_MODEL",
        "MORALSTACK_HINDSIGHT_MAX_RETRIES",
        "MORALSTACK_HINDSIGHT_MAX_TOKENS",
        "MORALSTACK_HINDSIGHT_TEMPERATURE",
        "MORALSTACK_HINDSIGHT_TOP_P",
        "MORALSTACK_HINDSIGHT_WEIGHT_SAFETY",
        "MORALSTACK_HINDSIGHT_WEIGHT_HELPFULNESS",
        "MORALSTACK_HINDSIGHT_WEIGHT_HONESTY",
        "MORALSTACK_HINDSIGHT_REFUSE_THRESHOLD",
        "MORALSTACK_HINDSIGHT_REVISE_THRESHOLD",
        "MORALSTACK_HINDSIGHT_USE_BATCH_EVALUATION",
        "MORALSTACK_HINDSIGHT_ENABLE_CACHING",
        # Orchestrator (optional overrides)
        "MORALSTACK_ORCHESTRATOR_MAX_DELIBERATION_CYCLES",
        "MORALSTACK_ORCHESTRATOR_RISK_LOW_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_RISK_MEDIUM_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_TIMEOUT_MS",
        "MORALSTACK_ORCHESTRATOR_ENABLE_PERSPECTIVES",
        "MORALSTACK_ORCHESTRATOR_NUM_SIMULATIONS",
        "MORALSTACK_ORCHESTRATOR_MIN_HINDSIGHT_SCORE",
        "MORALSTACK_ORCHESTRATOR_MAX_CRITICAL_VIOLATIONS",
        "MORALSTACK_ORCHESTRATOR_EARLY_EXIT_HINDSIGHT_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATION",
        "MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT",
        "MORALSTACK_ORCHESTRATOR_SAFE_RESPONSE_ON_ERROR",
        "MORALSTACK_ORCHESTRATOR_SKIP_OPTIONAL_MODULES_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_SOFT_TIMEOUT_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_PARALLEL_MODULE_CALLS",
        "MORALSTACK_ORCHESTRATOR_ENABLE_SIMULATOR_GATING",
        "MORALSTACK_ORCHESTRATOR_ENABLE_HINDSIGHT_GATING",
        "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SEMANTIC_HARM_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_DELTA_CHARS_THRESHOLD",
        "MORALSTACK_ORCHESTRATOR_SIMULATOR_GATE_SKIP_MAX_PRIOR_SEMANTIC_HARM",
        "MORALSTACK_ORCHESTRATOR_BORDERLINE_REFUSE_UPPER",
        "MORALSTACK_ORCHESTRATOR_PARALLEL_CRITIC_WITH_MODULES",
        "MORALSTACK_ORCHESTRATOR_ENABLE_DYNAMIC_PARALLEL_SCHEDULER",
        "MORALSTACK_ORCHESTRATOR_ENABLE_SPECULATIVE_GENERATION",
    }
)


def _purge_empty_env_vars() -> None:
    """Remove env vars that are set to empty string for known optional keys.

    Third-party libraries (e.g. openai, httpx) read env vars directly.
    An empty OPENAI_BASE_URL="" makes the client attempt a request to ""
    which fails with 'Request URL is missing an http:// or https:// protocol'.
    """
    for key in _OPTIONAL_ENV_VARS:
        val = os.environ.get(key)
        if val is not None and val.strip() == "":
            del os.environ[key]


def _find_project_root() -> Path | None:
    """Find project root: package location first (reliable for installed commands), then cwd."""
    _here = Path(__file__).resolve()
    pkg_root = _here.parent.parent.parent
    if (pkg_root / ".env").exists() or (pkg_root / "pyproject.toml").exists():
        return pkg_root
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / "pyproject.toml").exists() or (parent / ".env").exists():
            return parent
    return None


def load_env() -> bool:
    """
    Load .env from project root into os.environ.

    Returns True if a .env file was found and loaded, False otherwise.
    Non-empty .env values always override pre-existing environment variables.
    After loading, empty-valued optional vars are removed to prevent
    third-party libraries from misinterpreting them.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False

    root = _find_project_root()
    if root is None:
        return False

    env_path = root / ".env"
    if not env_path.exists():
        return False

    result = load_dotenv(env_path, override=True)
    _purge_empty_env_vars()
    return result


def _env_candidate_paths() -> list[Path]:
    """Return candidate directories for .env, in search order."""
    candidates: list[Path] = []
    _here = Path(__file__).resolve()
    pkg_root = _here.parent.parent.parent
    candidates.append(pkg_root)
    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        if p not in candidates:
            candidates.append(p)
    return candidates

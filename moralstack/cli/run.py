#!/usr/bin/env python3
"""
MoralStack Runtime CLI - Interactive shell for MoralStack.

Backwards-compatible re-exports. Implementation lives in moralstack/cli/ package.
"""

from moralstack.cli.loader import ModuleLoader
from moralstack.cli.mocks import (
    MockConstitutionStore,
    MockCritic,
    MockHindsight,
    MockPerspectives,
    MockPolicy,
    MockRiskEstimator,
    MockSimulator,
)
from moralstack.cli.models import (
    CLIConfig,
    DecisionReason,
    PhaseResult,
    PhaseType,
    _parse_critic_trace,
    _parse_hindsight_trace,
    _parse_perspectives_trace,
    _parse_policy_trace,
    _parse_risk_trace,
    _parse_simulator_trace,
    path_reason_from_risk_and_action,
)
from moralstack.cli.shell import MoralStackCLI, main, parse_args

__all__ = [
    "MoralStackCLI",
    "main",
    "parse_args",
    "CLIConfig",
    "DecisionReason",
    "PhaseType",
    "PhaseResult",
    "path_reason_from_risk_and_action",
    "_parse_risk_trace",
    "_parse_policy_trace",
    "_parse_critic_trace",
    "_parse_simulator_trace",
    "_parse_hindsight_trace",
    "_parse_perspectives_trace",
    "MockPolicy",
    "MockRiskEstimator",
    "MockCritic",
    "MockSimulator",
    "MockHindsight",
    "MockPerspectives",
    "MockConstitutionStore",
    "ModuleLoader",
]

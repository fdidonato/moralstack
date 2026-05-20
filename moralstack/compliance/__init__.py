"""
Developer Contract Compliance Layer (DCCL).

Reference: dccl_specification_v0.3.md
Introduced in: MoralStack 0.2 (Commit 1 - Foundation)

The DCCL is a new architectural component that evaluates whether a user request
invokes a behavior explicitly authorized by the deployer's developer contract.
It runs after the policy speculative and before the risk_estimator, coordinating
the rest of the pipeline via a cooperative early-return mechanism.

Public API:
    - DeveloperContractComplianceLayer: main entry point
    - ComplianceVerdict: structured result of an evaluation
    - ComplianceDecision: enum of possible decisions
    - ComplianceSignal: signal propagated to downstream modules
    - StructuredRule: deployer-declarable rule
"""

from __future__ import annotations

from moralstack.compliance.dccl import DeveloperContractComplianceLayer
from moralstack.compliance.types import (
    ActionType,
    ComplianceDecision,
    ComplianceSignal,
    ComplianceVerdict,
    EvaluationPath,
    MatchedRule,
    StructuredRule,
    TriggerType,
)

__all__ = [
    "DeveloperContractComplianceLayer",
    "ComplianceVerdict",
    "ComplianceDecision",
    "ComplianceSignal",
    "EvaluationPath",
    "MatchedRule",
    "StructuredRule",
    "TriggerType",
    "ActionType",
]

"""
Public exceptions for the MoralStack SDK.

Internal pipeline exceptions (RiskEstimationError, CritiqueError, etc.)
are never exposed to callers: they are translated to GovernanceError at the boundary.
"""

from __future__ import annotations


class GovernanceError(Exception):
    """Base error for the MoralStack SDK."""


class GovernancePipelineError(GovernanceError):
    """
    The deliberative pipeline failed.

    May indicate: provider unreachable, internal controller error,
    or deliberation timeout. The ``cause`` field holds the original exception.
    """

    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause

    def __str__(self) -> str:
        base = super().__str__()
        if self.cause is not None:
            return f"{base} (caused by: {type(self.cause).__name__}: {self.cause})"
        return base


class GovernanceTimeoutError(GovernancePipelineError):
    """Timeout during pipeline deliberation."""


class GovernanceConfigError(GovernanceError):
    """
    Invalid configuration.

    Examples: missing API key, non-existent overlay, incompatible parameters.
    """

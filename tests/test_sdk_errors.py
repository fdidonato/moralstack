"""Tests for moralstack.sdk.errors."""

import pytest

from moralstack.sdk.errors import (
    GovernanceConfigError,
    GovernanceError,
    GovernancePipelineError,
    GovernanceTimeoutError,
)


class TestGovernanceErrorHierarchy:
    def test_governance_error_is_exception(self):
        err = GovernanceError("test")
        assert isinstance(err, Exception)

    def test_pipeline_error_is_governance_error(self):
        err = GovernancePipelineError("pipeline failed")
        assert isinstance(err, GovernanceError)

    def test_timeout_error_is_pipeline_error(self):
        err = GovernanceTimeoutError("timeout", cause=None)
        assert isinstance(err, GovernancePipelineError)
        assert isinstance(err, GovernanceError)

    def test_config_error_is_governance_error(self):
        err = GovernanceConfigError("bad config")
        assert isinstance(err, GovernanceError)

    def test_pipeline_error_with_cause(self):
        original = ValueError("original error")
        err = GovernancePipelineError("wrapped", cause=original)
        assert err.cause is original
        assert "original error" in str(err)
        assert "wrapped" in str(err)

    def test_pipeline_error_without_cause(self):
        err = GovernancePipelineError("no cause")
        assert err.cause is None
        assert "no cause" in str(err)

    def test_can_raise_and_catch(self):
        with pytest.raises(GovernanceError):
            raise GovernancePipelineError("test")

    def test_can_catch_specific_subclass(self):
        with pytest.raises(GovernancePipelineError):
            raise GovernanceTimeoutError("timeout", cause=None)

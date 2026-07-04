"""
Tests for dynamic framework version in cli/report.py output.
"""

from __future__ import annotations

import moralstack


class TestVersionIsDynamic:
    def test_pyproject_version_matches_package(self):
        """The package __version__ must match the pyproject declared version."""
        assert moralstack.__version__ == "0.6.1"

    def test_report_uses_current_version(self):
        """The report.py module references moralstack.__version__ dynamically (not hardcoded)."""
        import inspect

        from moralstack.cli import report

        src = inspect.getsource(report)
        # The robust check: the module must reference the package __version__
        # via the local `_moralstack_version` import or via the symbol itself.
        assert (
            "_moralstack_version" in src or "__version__" in src
        ), "report.py must reference moralstack.__version__ dynamically"
        # Defensive: any remaining hardcoded `0.1.0` token would indicate the
        # parametrization was incomplete.
        assert "0.1.0" not in src, "report.py still contains hardcoded '0.1.0'; expected dynamic version resolution"

"""
Unit tests for the decision axis mapping (_axis_val).

Verifies that known inputs produce the expected outputs, without changing
thresholds or semantics. Covers CLEAR, NONE, AMBIGUOUS, unexpected values.
"""

import sys
from pathlib import Path

# Using private functions is intentional: it serves semantic-invariance tests.
# It is NOT part of the public API.
from moralstack.orchestration.decision_service import _axis_val

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _obj(value: str):
    """Object with .value to simulate an enum."""
    return type("_Axis", (), {"value": value})()


def test_axis_val_clear_returns_high():
    """CLEAR -> HIGH (legacy intent clarity)."""
    assert _axis_val(_obj("CLEAR")) == "HIGH"
    assert _axis_val(_obj("clear")) == "HIGH"


def test_axis_val_none_returns_high():
    """NONE (category) -> HIGH (legacy)."""
    assert _axis_val(_obj("NONE")) == "HIGH"
    assert _axis_val(_obj("none")) == "HIGH"


def test_axis_val_ambiguous_returns_low():
    """AMBIGUOUS -> LOW."""
    assert _axis_val(_obj("AMBIGUOUS")) == "LOW"
    assert _axis_val(_obj("ambiguous")) == "LOW"


def test_axis_val_pass_through():
    """LOW, MEDIUM, HIGH pass-through."""
    assert _axis_val(_obj("LOW")) == "LOW"
    assert _axis_val(_obj("MEDIUM")) == "MEDIUM"
    assert _axis_val(_obj("HIGH")) == "HIGH"


def test_axis_val_none_input_returns_low():
    """Input None (absent) -> LOW (fallback)."""
    assert _axis_val(None) == "LOW"


def test_axis_val_unexpected_values_fallback_to_low():
    """Unmapped values -> LOW (explicit fallback)."""
    assert _axis_val(_obj("MALICIOUS")) == "LOW"
    assert _axis_val(_obj("UNKNOWN")) == "LOW"
    assert _axis_val(_obj("")) == "LOW"
    assert _axis_val(_obj("xyz")) == "LOW"


def test_axis_val_no_value_attr_fallback():
    """Object without .value -> LOW."""
    assert _axis_val(object()) == "LOW"


def test_axis_val_with_real_enums():
    """Integration with real enums from models.risk."""
    from moralstack.models.risk import ActionabilityRisk, IntentClarity, MisusePlausibility

    assert _axis_val(IntentClarity.LOW) == "LOW"
    assert _axis_val(IntentClarity.MEDIUM) == "MEDIUM"
    assert _axis_val(IntentClarity.HIGH) == "HIGH"
    assert _axis_val(MisusePlausibility.LOW) == "LOW"
    assert _axis_val(MisusePlausibility.HIGH) == "HIGH"
    assert _axis_val(ActionabilityRisk.MEDIUM) == "MEDIUM"

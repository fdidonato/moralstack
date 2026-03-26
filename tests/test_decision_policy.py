"""
Test deterministici per la policy formale SAFE_COMPLETE (decision_policy).

Verifica le funzioni pure safe_complete_required e normal_complete_allowed
e la derivazione intent_type da request_type.
"""

from moralstack.runtime.decision_policy import (
    intent_type_from_request_type,
    normal_complete_allowed,
    safe_complete_required,
)

# -----------------------------------------------------------------------------
# safe_complete_required
# -----------------------------------------------------------------------------


def test_safe_complete_required_sensitive_support():
    """risk_level SENSITIVE + intent_type SUPPORT → SAFE_COMPLETE richiesto."""
    assert safe_complete_required("sensitive", "support", None) is True
    assert safe_complete_required("SENSITIVE", "SUPPORT", None) is True


def test_safe_complete_required_sensitive_advice():
    """risk_level SENSITIVE + intent_type ADVICE → SAFE_COMPLETE richiesto."""
    assert safe_complete_required("sensitive", "advice", None) is True


def test_safe_complete_required_morally_nuanced_support():
    """risk_level MORALLY_NUANCED + intent_type SUPPORT → SAFE_COMPLETE richiesto."""
    assert safe_complete_required("morally_nuanced", "support", None) is True


def test_safe_complete_required_healthcare_non_benign():
    """domain_overlay healthcare + risk_level != BENIGN → SAFE_COMPLETE richiesto."""
    assert safe_complete_required("sensitive", "factual", "healthcare") is True
    assert safe_complete_required("morally_nuanced", None, "healthcare") is True
    assert safe_complete_required("potentially_harmful", "advice", "mental_health") is True
    assert safe_complete_required("sensitive", "factual", "legal") is True
    assert safe_complete_required("sensitive", "factual", "financial") is True


def test_safe_complete_required_healthcare_benign_false():
    """domain_overlay healthcare + risk_level BENIGN → non richiesto da questa regola
    (benign gestito dopo)."""
    assert safe_complete_required("benign", "factual", "healthcare") is False


def test_safe_complete_required_sensitive_factual_no_domain_false():
    """sensitive + factual + no domain → SAFE_COMPLETE non richiesto (NORMAL consentito)."""
    assert safe_complete_required("sensitive", "factual", None) is False
    assert safe_complete_required("sensitive", "explanation", None) is False


# -----------------------------------------------------------------------------
# normal_complete_allowed
# -----------------------------------------------------------------------------


def test_normal_complete_allowed_sensitive_factual_no_domain():
    """sensitive + factual + domain None → NORMAL_COMPLETE consentito."""
    assert normal_complete_allowed("sensitive", "factual", None) is True
    assert normal_complete_allowed("sensitive", "explanation", None) is True
    assert normal_complete_allowed("morally_nuanced", "factual", None) is True


def test_normal_complete_allowed_with_domain_false():
    """Con domain_overlay impostato → NORMAL_COMPLETE non consentito (per quella policy)."""
    assert normal_complete_allowed("sensitive", "factual", "healthcare") is False
    assert normal_complete_allowed("sensitive", "factual", "legal") is False


def test_normal_complete_allowed_support_false():
    """intent_type support/advice → NORMAL_COMPLETE non nella banda allowed (min=S)."""
    assert normal_complete_allowed("sensitive", "support", None) is False
    assert normal_complete_allowed("sensitive", "advice", None) is False


def test_normal_complete_allowed_benign_false():
    """risk_level benign non rientra nella eccezione (benign ha già min=N max=N)."""
    assert normal_complete_allowed("benign", "factual", None) is False


# -----------------------------------------------------------------------------
# intent_type_from_request_type
# -----------------------------------------------------------------------------


def test_intent_type_from_request_type_support():
    assert intent_type_from_request_type("crisis_support") == "support"
    assert intent_type_from_request_type("support_request") == "support"


def test_intent_type_from_request_type_factual():
    assert intent_type_from_request_type("factual_query") == "factual"
    assert intent_type_from_request_type("creative_request") == "factual"


def test_intent_type_from_request_type_advice():
    assert intent_type_from_request_type("ethical_dilemma") == "advice"
    # Default (risk_score=0.5, intent_operational=False): 0.5 non è < 0.5, quindi "advice"
    assert intent_type_from_request_type("sensitive_topic") == "advice"


def test_intent_type_sensitive_topic_factual_when_informational():
    """sensitive_topic + non-operational + low risk → factual (over-governance fix)."""
    assert intent_type_from_request_type("sensitive_topic", intent_operational=False, risk_score=0.4) == "factual"
    assert intent_type_from_request_type("sensitive_topic", intent_operational=False, risk_score=0.35) == "factual"


def test_intent_type_sensitive_topic_advice_when_operational():
    """sensitive_topic + operational → advice (safety preserved)."""
    assert intent_type_from_request_type("sensitive_topic", intent_operational=True, risk_score=0.4) == "advice"


def test_intent_type_sensitive_topic_advice_when_high_risk():
    """sensitive_topic + high risk_score → advice (safety preserved)."""
    assert intent_type_from_request_type("sensitive_topic", intent_operational=False, risk_score=0.6) == "advice"
    assert intent_type_from_request_type("sensitive_topic", intent_operational=False, risk_score=0.5) == "advice"

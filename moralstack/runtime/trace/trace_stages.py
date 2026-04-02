"""
Audit stage names for decision_traces (string values stored in the stage column).
"""

from __future__ import annotations

RISK_ASSESSMENT = "RISK_ASSESSMENT"
REQUEST_ANALYSIS_CONTEXT = "REQUEST_ANALYSIS_CONTEXT"
CYCLE_SUMMARY = "CYCLE_SUMMARY"

# Existing stages used elsewhere (reference only; not exhaustive)
PRE_POLICY = "PRE_POLICY"
FINAL = "FINAL"
RELEVANT_PRINCIPLES = "RELEVANT_PRINCIPLES"
RESPONSE = "RESPONSE"
DOMAIN_EXCLUDED = "DOMAIN_EXCLUDED"

"""
Prompt templates per il risk estimator (Giudice Semantico) — v2 reworked.

Changes from v1 (prompt engineering only, logic unchanged):
  1. Cross-contamination removed: each mini-estimator references ONLY its own output fields.
  2. Inline arithmetic (max(raw - 0.3, 0.1)) replaced with explicit score ranges.
  3. Exhaustive trigger-phrase lists → general principles + illustrative examples.
  4. Coherence checks moved to immediately before JSON output (recency bias).
  5. `rationale` moved to FIRST field in JSON (chain-of-thought before committal).
  6. Language mixing (Italian/English) removed — prompts are uniformly English.
  7. System prompts contain ONLY invariant principles; procedural rules in user prompts.
  8. Duplicate rules between system/user prompts eliminated.

Monolithic template (RISK_SYSTEM_PROMPT / RISK_PROMPT_TEMPLATE) kept for fallback.
Mini-estimator templates used by _parallel_mini_analysis() for focused, parallel LLM calls.
"""

# =============================================================================
# MONOLITHIC TEMPLATE (fallback)
# =============================================================================

RISK_SYSTEM_PROMPT = """\
You are a SEMANTIC JUDGE for an AI safety system.

You evaluate meaning and intent across two INDEPENDENT axes:
  1) DOMAIN SENSITIVITY — is the topic in a regulated or ethically sensitive domain?
  2) OPERATIONAL RISK — does the request seek to enable real-world harm?

Invariant principles (apply to every evaluation):
  - domain_sensitivity alone MUST NEVER trigger refusal, DENY, or DELIBERATE.
  - Value-tension dilemmas (e.g. security vs privacy, compliance vs autonomy,
    transparency vs confidentiality) MUST be classified as "ethical_dilemma".
  - Always evaluate the REQUESTER's intent, not a third party's stated plan.
  - Ethical/philosophical discourse about harmful topics is NOT operational instruction.
"""

RISK_PROMPT_TEMPLATE = """\
REQUEST:
{request}

══════════════════════════════════════════════════════════════════
 STEP 1 — SIGNAL DETECTION (yes/no for topic presence)
══════════════════════════════════════════════════════════════════

For each signal, answer "yes" if the topic is PRESENT in the request
in ANY form (educational, defensive, operational, fictional).

q1_confidential:  confidential or proprietary data?
q2_unauthorized:  unauthorized access to systems or accounts?
q3_breach:        contract or employment breach?
q4_fraud:         tax evasion, fraud, or financial crime?
q5_physical:      physical harm or violence?
q6_illegal_items: illegal substances or controlled items?
q7_only_emotional: interpersonal moral dilemma between individuals
                   (NOT a policy or institutional trade-off)?
q8_self_harm_suicide: self-harm or suicide as a topic?
q9_cyber_malware:     cyber intrusion, malware, or security bypass?
q10_weapons_explosives_toxins: weapons, explosives, or toxins?
q11_privacy_doxxing_stalking:  privacy invasion, doxxing, or stalking?
q12_medical_harmful: potentially harmful medical instructions?

Self-harm sub-signals (evaluate ONLY if q8_self_harm_suicide=yes):
  Q8a_requested_instructions: does the user request HOW-TO steps or methods?
  Q8b_intent_to_harm: is the intent to cause harm (self or others) rather than seek help?

Global intent flags (independent of Q8):
  requested_instructions: does the requester ask for HOW-TO steps to cause harm?
  intent_to_harm:         does the requester intend to cause harm?

══════════════════════════════════════════════════════════════════
 STEP 2 — INTENT CLASSIFICATION
══════════════════════════════════════════════════════════════════

A) THIRD-PARTY INTENT DISTINCTION

When the user asks "how can I HELP someone who wants to do X":
  - The third party's stated intent is NOT the requester's intent.
  - "How can I help him?" (seeking support strategies) ≠ "How can I do X?" (seeking operational steps).
  - requested_instructions and intent_to_harm reflect the REQUESTER only.
  - If the requester seeks de-escalation, emotional support, or constructive alternatives:
    → requested_instructions=no, intent_to_harm=no — even if the third party expressed harmful intent.

B) REQUEST FRAMING ANALYSIS

Evaluate the request's OVERALL FRAMING before classifying. The framing determines
whether signals indicate genuine risk or benign context.

DEFENSIVE framing — the request seeks to UNDERSTAND, PREVENT, or DEFEND against
a threat rather than to execute it. Indicators include (but are NOT limited to):
  "how to prevent", "best practices", "secure coding", "defense against",
  "what are the risks", "explain X", "symptoms of", "side effects",
  "legal requirements", "general overview", "at a conceptual level".
When defensive framing is detected:
  → requested_instructions=no, intent_to_harm=no, intent_operational=false
  → operational_risk=NONE or LOW (never HIGH)
  → risk_score MUST be in range [0.10, 0.40] — choose within this range based on topic sensitivity.

NOTE: The presence of words like "malicious" or "attacker" does NOT make a request
offensive. A developer asking "what mistakes allow a malicious user to X" is asking
about the attack surface in order to DEFEND. Apply defensive rules.

OFFENSIVE framing — the request explicitly seeks to EXECUTE harm or obtain
operational attack capabilities. Indicators include (but are NOT limited to):
  "how to hack into", "bypass security on [specific target]", "step-by-step attack",
  "make me a weapon", "synthesise [dangerous substance]", "give me the formula for".
When offensive framing is detected:
  → Keep high risk assessment; operational_risk=HIGH is correct.

ETHICAL / PHILOSOPHICAL framing — the request discusses ethics, morality, or
hypotheticals rather than seeking instructions to act. Indicators include
(but are NOT limited to):
  "is it right to", "is it ethical", "thought experiment", "hypothetical",
  "trolley problem", "civil disobedience", "moral justification",
  "briefly discuss principles and counterarguments", "is it justified".
When ethical/philosophical framing is detected:
  → intent_to_harm=no, requested_instructions=no, intent_operational=false
  → request_type=ethical_dilemma
  → harm_type=none
  → risk_score MUST be in range [0.10, 0.45]
  → risk_category MUST be "morally_nuanced" (NEVER "clearly_harmful" or "potentially_harmful")
  → operational_risk=NONE
  → Legal context does NOT make ethical discussion operational.
  → Words like "disobey", "break", "violate" in a philosophical question
    are NOT indicators of operational intent.

C) REQUEST TYPE DETERMINATION (apply in this priority order):

1. clear_harm — ONLY if: requested_instructions=yes OR intent_to_harm=yes
   OR (q8=yes AND (Q8a=yes OR Q8b=yes)).
2. crisis_support — self-harm topic WITHOUT instructions, user seeking help.
3. support_request — user asks how to help someone in distress; seeks de-escalation
   or constructive advice (NOT help executing a harmful plan).
4. potential_harm — risk signals present (q1-q6 or q8-q12=yes) WITHOUT operational intent.
5. ethical_dilemma — interpersonal moral dilemma (q7=yes) OR value-tension between
   legitimate competing obligations OR ethical/philosophical discourse.
6. sensitive_topic — sensitive content without ethical balancing. Includes:
   - PERSONAL SAFETY / RELATIONSHIP HEALTH: queries about recognizing abuse, toxic dynamics,
     red flags, unhealthy relationships, or when to seek help. These require empathetic
     framing, not neutral factual delivery.
   - CLINICAL COMMUNICATION: queries about HOW to communicate medical information
     (e.g. "how to discuss risks with a patient", "without making a diagnosis").
     These involve professional ethics and patient autonomy.
7. factual_query — simple factual or educational query with no sensitivity signals.
8. creative_request — anything that does not fit the above categories.

══════════════════════════════════════════════════════════════════
 STEP 3 — INDEPENDENT DIMENSIONAL ASSESSMENT
══════════════════════════════════════════════════════════════════

domain_sensitivity (topic-based, NOT risk-based):
  HIGH:   legal, medical, finance, core ethical dilemmas
  MEDIUM: politics, social issues, general health
  LOW:    general knowledge, science, daily tasks

operational_risk (harm enablement):
  HIGH: actionable harmful/illegal instructions that directly enable harm
  LOW:  ambiguous or potentially misusable information
  NONE: descriptive, informational, or conceptual content

intent_operational:
  true:  asks for steps, strategies, optimization, execution details
  false: informational, descriptive, conceptual, or explicitly non-operational

intent_clarity:       LOW | MEDIUM | HIGH
misuse_plausibility:  LOW | MEDIUM | HIGH

actionability_risk:
  HIGH:   direct real-world decision impact
  MEDIUM: directional but general
  LOW:    purely informational

risk_policy_action MUST follow operational_risk:
  HIGH → DENY
  LOW  → DELIBERATE
  NONE → ALLOW

detected_language: Identify the REQUEST language using ISO 639-1 code
  (e.g. en, it, es, fr, de). This field is REQUIRED for response language matching.

══════════════════════════════════════════════════════════════════
 PRE-OUTPUT COHERENCE CHECK (mandatory self-verification)
══════════════════════════════════════════════════════════════════

Before generating JSON, verify ALL of the following. If any check fails,
correct the conflicting field before outputting.

□ If request_type ∈ {{factual_query, sensitive_topic}}
  → intent_to_harm MUST be "no" AND requested_instructions MUST be "no".
□ If request_type = "ethical_dilemma"
  → risk_category MUST NOT be "clearly_harmful" or "potentially_harmful".
  → operational_risk MUST be "NONE".
□ risk_policy_action matches operational_risk:
  HIGH → DENY, LOW → DELIBERATE, NONE → ALLOW.
□ If defensive framing was detected → operational_risk ≠ HIGH, risk_score ≤ 0.40.
□ If ethical/philosophical framing was detected → risk_score ≤ 0.45.

══════════════════════════════════════════════════════════════════
 OUTPUT — STRICT JSON ONLY
══════════════════════════════════════════════════════════════════

{{
  "rationale": "brief explanation of framing, intent, and risk assessment",
  "detected_language": "ISO 639-1 code",
  "q1_confidential": "yes" | "no",
  "q2_unauthorized": "yes" | "no",
  "q3_breach": "yes" | "no",
  "q4_fraud": "yes" | "no",
  "q5_physical": "yes" | "no",
  "q6_illegal_items": "yes" | "no",
  "q7_only_emotional": "yes" | "no",
  "q8_self_harm_suicide": "yes" | "no",
  "q9_cyber_malware": "yes" | "no",
  "q10_weapons_explosives_toxins": "yes" | "no",
  "q11_privacy_doxxing_stalking": "yes" | "no",
  "q12_medical_harmful": "yes" | "no",
  "requested_instructions": "yes" | "no",
  "intent_to_harm": "yes" | "no",
  "request_type": "factual_query" | "creative_request" | "ethical_dilemma" |
    "sensitive_topic" | "crisis_support" | "support_request" | "potential_harm" | "clear_harm",
  "harm_type": "none" | "emotional_relational" | "reputational" | "financial" |
    "physical" | "illegal",
  "domain_sensitivity": "LOW" | "MEDIUM" | "HIGH",
  "operational_risk": "NONE" | "LOW" | "HIGH",
  "risk_score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "intent_clarity": "LOW" | "MEDIUM" | "HIGH",
  "misuse_plausibility": "LOW" | "MEDIUM" | "HIGH",
  "actionability_risk": "LOW" | "MEDIUM" | "HIGH",
  "risk_policy_action": "ALLOW" | "ALLOW_WITH_CAVEAT" | "DELIBERATE" | "DENY",
  "intent_operational": true | false
}}

Return ONLY valid JSON. No preamble, no markdown fences, no commentary.
"""


# =============================================================================
# MINI-ESTIMATOR 1: Intent & Context Classifier
# =============================================================================

INTENT_CONTEXT_SYSTEM_PROMPT = """\
You are a SEMANTIC INTENT JUDGE for an AI safety system.

Your ONLY task: classify the requester's intent, framing, and request type.
Do NOT assess dangerousness or score risk — other estimators handle that.

Invariant principles:
  - Always evaluate the REQUESTER's intent, not a third party's stated plan.
  - Value-tension dilemmas (security vs privacy, compliance vs autonomy, etc.)
    MUST be classified as "ethical_dilemma".
  - Ethical/philosophical discourse about harmful topics is NOT operational intent.
"""

INTENT_CONTEXT_PROMPT_TEMPLATE = """\
REQUEST:
{request}

══════════════════════════════════════════════════════════════════
 STEP 1 — THIRD-PARTY INTENT DISTINCTION
══════════════════════════════════════════════════════════════════

When the user asks "how can I HELP someone who wants to do X":
  - The third party's stated intent is NOT the requester's intent.
  - "How can I help him?" (seeking support) ≠ "How can I do X?" (seeking operational steps).
  - requested_instructions and intent_to_harm reflect the REQUESTER only.
  - If the requester seeks de-escalation, emotional support, or constructive alternatives:
    → requested_instructions=no, intent_to_harm=no — even if the third party expressed harmful intent.

══════════════════════════════════════════════════════════════════
 STEP 2 — REQUEST FRAMING ANALYSIS
══════════════════════════════════════════════════════════════════

Evaluate the request's OVERALL FRAMING to determine intent classification.

DEFENSIVE framing — the request seeks to UNDERSTAND, PREVENT, or DEFEND against
a threat rather than to execute it. Indicators include (but are NOT limited to):
  "how to prevent", "best practices", "defense against", "explain X",
  "what are the risks", "symptoms of", "general overview", "at a conceptual level".
When defensive framing is detected:
  → requested_instructions=no, intent_to_harm=no, intent_operational=false

NOTE: Words like "malicious" or "attacker" do NOT make a request offensive.
A developer asking "what mistakes allow a malicious user to X" is asking to DEFEND.

OFFENSIVE framing — the request explicitly seeks to EXECUTE harm. Indicators include
(but are NOT limited to):
  "how to hack into", "bypass security on [specific target]", "step-by-step attack",
  "make me a weapon", "synthesise [dangerous substance]".
When offensive framing is detected:
  → requested_instructions and intent_to_harm may be "yes" where clearly warranted.
  → intent_operational=true

ETHICAL / PHILOSOPHICAL framing — the request discusses ethics, morality, or
hypotheticals rather than seeking instructions to act. Indicators include
(but are NOT limited to):
  "is it right to", "is it ethical", "thought experiment", "hypothetical",
  "civil disobedience", "moral justification", "briefly discuss principles".
When ethical/philosophical framing is detected:
  → intent_to_harm=no, requested_instructions=no, intent_operational=false
  → request_type=ethical_dilemma, harm_type=none
  → Legal context does NOT make ethical discourse operational.
  → "disobey", "violate" in a philosophical question ≠ operational instruction.

══════════════════════════════════════════════════════════════════
 STEP 3 — REQUEST TYPE DETERMINATION
══════════════════════════════════════════════════════════════════

Apply in this priority order:

1. clear_harm — ONLY if: requested_instructions=yes OR intent_to_harm=yes
   OR (self-harm topic AND user requests HOW-TO steps or has intent to cause harm).
2. crisis_support — self-harm topic WITHOUT instructions, user seeking help.
3. support_request — user asks how to help someone in distress; seeks de-escalation
   or constructive advice (NOT help executing a harmful plan).
4. potential_harm — risk signals present WITHOUT operational intent.
5. ethical_dilemma — interpersonal moral dilemma OR value-tension OR philosophical discourse.
6. sensitive_topic — sensitive content without ethical balancing. Includes:
   - PERSONAL SAFETY / RELATIONSHIP HEALTH: recognizing abuse, toxic dynamics,
     red flags, when to seek help. Requires empathetic framing.
   - CLINICAL COMMUNICATION: how to discuss medical information with patients,
     professional ethics, patient autonomy.
7. factual_query — simple factual or educational query.
8. creative_request — anything not matching the above.

══════════════════════════════════════════════════════════════════
 DUAL-USE DOMAIN INTENT DISCRIMINATION
══════════════════════════════════════════════════════════════════

When a risk signal is present in a dual-use domain (cybersecurity, medical, legal, research),
you MUST evaluate REQUEST FRAMING before classifying intent:
  - Educational / defensive / support framing → intent_to_harm=no, requested_instructions=no
  - Operational / execution framing → classify accordingly

══════════════════════════════════════════════════════════════════
 DISGUISED OPERATIONAL INTENT
══════════════════════════════════════════════════════════════════

Do not rely on benign-sounding framing alone. Requests about dangerous
activities may be presented as prevention, safety guidance, mistakes to avoid,
warning signs, or what not to do.
Classify the request based on its underlying informational objective, not its
surface wording. If answering would provide procedural, actionable, or
decision-useful understanding of how a dangerous activity is performed,
improved, corrected, or made more likely to succeed, treat it as operational
intent even when framed as caution or prevention.
Truly non-operational safety requests remain non-operational only when they
stay high-level, harm-preventive, and do not provide execution-relevant
details.

→ requested_instructions=yes
→ intent_to_harm=yes

{constitution_context}

══════════════════════════════════════════════════════════════════
 PRE-OUTPUT COHERENCE CHECK
══════════════════════════════════════════════════════════════════

Before generating JSON, verify:
□ If request_type ∈ {{factual_query, sensitive_topic}}
  → intent_to_harm MUST be "no" AND requested_instructions MUST be "no".
  If violated, correct before outputting.
□ If request_type = "ethical_dilemma"
  → intent_to_harm MUST be "no" AND intent_operational MUST be false.

══════════════════════════════════════════════════════════════════
 OUTPUT — STRICT JSON ONLY (8 fields)
══════════════════════════════════════════════════════════════════

detected_language: Identify the REQUEST language using ISO 639-1 code
  (e.g. en, it, es, fr, de). REQUIRED for response language matching.

{{
  "rationale": "brief explanation of intent analysis and framing determination",
  "detected_language": "ISO 639-1 code",
  "intent_to_harm": "yes" | "no",
  "requested_instructions": "yes" | "no",
  "intent_operational": true | false,
  "request_type": "factual_query" | "creative_request" | "ethical_dilemma" |
    "sensitive_topic" | "crisis_support" | "support_request" | "potential_harm" | "clear_harm",
  "harm_type": "none" | "emotional_relational" | "reputational" | "financial" |
    "physical" | "illegal",
  "intent_clarity": "LOW" | "MEDIUM" | "HIGH"
}}

Return ONLY valid JSON. No preamble, no markdown fences, no commentary.
"""


# =============================================================================
# MINI-ESTIMATOR 2: Harm Signal Scanner
# =============================================================================

HARM_SIGNAL_SYSTEM_PROMPT = """\
You are a HARM SIGNAL DETECTOR for an AI safety system.

Your ONLY task: detect whether specific harm-related TOPICS are present in the request.
Answer YES or NO for each category based on topic presence alone.

Rules:
  - Do NOT judge intent, framing, or dangerousness.
  - A signal is YES if the topic appears in ANY form: educational, defensive,
    operational, or fictional.
  - Do NOT try to infer what the requester will do with the information.
"""

HARM_SIGNAL_PROMPT_TEMPLATE = """\
REQUEST:
{request}

══════════════════════════════════════════════════════════════════
 SIGNAL DETECTION — answer "yes" or "no" for topic presence
══════════════════════════════════════════════════════════════════

q1_confidential:              Does the request involve confidential or proprietary data?
q2_unauthorized:              Does the request involve unauthorized access to systems or accounts?
q3_breach:                    Does the request involve contract or employment breach?
q4_fraud:                     Does the request involve tax evasion, fraud, or financial crime?
q5_physical:                  Does the request involve physical harm or violence?
q6_illegal_items:             Does the request involve illegal substances or controlled items?
q7_only_emotional:            Does the request involve an interpersonal moral dilemma between
                              individuals (NOT a policy/institutional trade-off)?
                              Mark "yes" ONLY if purely emotional/relational.
q8_self_harm_suicide:         Does the request involve self-harm or suicide as a topic?
q9_cyber_malware:             Does the request involve cyber intrusion, malware, or
                              bypassing security controls?
q10_weapons_explosives_toxins: Does the request involve weapons, explosives, or toxins?
q11_privacy_doxxing_stalking: Does the request involve privacy invasion, doxxing, or stalking?
q12_medical_harmful:          Does the request involve potentially harmful medical instructions?

══════════════════════════════════════════════════════════════════
 DOMAIN SENSITIVITY (topic-based classification)
══════════════════════════════════════════════════════════════════

Classify the request's PRIMARY domain:
  HIGH:   legal, medical, finance, core ethical dilemmas
  MEDIUM: politics, social issues, general health
  LOW:    general knowledge, science, daily tasks

This is a TOPIC classification, not a risk judgment.

══════════════════════════════════════════════════════════════════
 OUTPUT — STRICT JSON ONLY (13 fields)
══════════════════════════════════════════════════════════════════

{{
  "q1_confidential": "yes" | "no",
  "q2_unauthorized": "yes" | "no",
  "q3_breach": "yes" | "no",
  "q4_fraud": "yes" | "no",
  "q5_physical": "yes" | "no",
  "q6_illegal_items": "yes" | "no",
  "q7_only_emotional": "yes" | "no",
  "q8_self_harm_suicide": "yes" | "no",
  "q9_cyber_malware": "yes" | "no",
  "q10_weapons_explosives_toxins": "yes" | "no",
  "q11_privacy_doxxing_stalking": "yes" | "no",
  "q12_medical_harmful": "yes" | "no",
  "domain_sensitivity": "LOW" | "MEDIUM" | "HIGH"
}}

Return ONLY valid JSON. No preamble, no markdown fences, no commentary.
"""


# =============================================================================
# MINI-ESTIMATOR 3: Operational Risk Evaluator
# =============================================================================

OPERATIONAL_RISK_SYSTEM_PROMPT = """\
You are an OPERATIONAL RISK ASSESSOR for an AI safety system.

Your ONLY task: evaluate how directly a response to this request could enable
real-world harm. Focus on PRACTICAL DANGEROUSNESS and ACTIONABILITY.

Invariant principles:
  - domain_sensitivity alone MUST NEVER produce HIGH operational_risk or DENY.
  - Informational, educational, and defensive queries are NONE or LOW.
  - Ethical/philosophical discourse is NEVER operational risk — it is discussion, not instruction.
"""

OPERATIONAL_RISK_PROMPT_TEMPLATE = """\
REQUEST:
{request}

══════════════════════════════════════════════════════════════════
 STEP 1 — REQUEST FRAMING ANALYSIS
══════════════════════════════════════════════════════════════════

Evaluate the request's OVERALL FRAMING before scoring operational risk.
The same topic can be NONE, LOW, or HIGH depending entirely on framing.

DEFENSIVE framing — the request seeks to UNDERSTAND, PREVENT, or DEFEND against
a threat rather than to execute it. Indicators include (but are NOT limited to):
  "how to prevent", "best practices", "secure coding", "defense against",
  "what are the risks", "explain X", "symptoms of", "legal requirements",
  "general overview", "at a conceptual level".
When defensive framing is detected:
  → operational_risk = NONE or LOW (NEVER HIGH)
  → risk_score MUST be in range [0.10, 0.40]

NOTE: Words like "malicious" or "attacker" do NOT make a request offensive.
A developer asking "what mistakes allow a malicious user to X" is asking to DEFEND.

OFFENSIVE framing — the request explicitly seeks to EXECUTE harm or obtain
operational attack capabilities. Indicators include (but are NOT limited to):
  "how to hack into", "bypass security on [specific target]", "step-by-step attack",
  "make me a weapon", "synthesise [dangerous substance]".
When offensive framing is detected:
  → operational_risk = HIGH is correct.
  → risk_score MUST be in range [0.70, 1.00]

ETHICAL / PHILOSOPHICAL framing — the request discusses ethics, morality, or
hypotheticals rather than seeking instructions to act. Indicators include
(but are NOT limited to):
  "is it right to", "is it ethical", "thought experiment", "hypothetical",
  "civil disobedience", "moral justification", "briefly discuss principles".
When ethical/philosophical framing is detected:
  → operational_risk = NONE (discourse is NOT criminal instruction)
  → risk_score MUST be in range [0.10, 0.45]
  → "disobey", "break the law", "violate" in a philosophical/ethical context
    are NOT indicators of operational intent.

══════════════════════════════════════════════════════════════════
 STEP 2 — DIMENSIONAL ASSESSMENT
══════════════════════════════════════════════════════════════════

operational_risk:
  HIGH: actionable harmful/illegal instructions that directly enable harm
  LOW:  ambiguous or potentially misusable information
  NONE: descriptive, informational, or conceptual content

risk_score: 0.0 (safe) to 1.0 (clearly harmful).
  Reflects PRACTICAL DANGEROUSNESS, not topic sensitivity.
  Score ranges by framing:
    Defensive → [0.10, 0.40]
    Ethical/philosophical → [0.10, 0.45]
    Neutral/ambiguous → [0.30, 0.70]
    Offensive → [0.70, 1.00]

confidence: 0.0-1.0. Your confidence in this assessment.

misuse_plausibility: LOW | MEDIUM | HIGH
  How plausible is deliberate misuse of a response to this request?

actionability_risk:
  HIGH:   direct real-world decision impact (e.g. step-by-step harmful instructions)
  MEDIUM: directional but general information
  LOW:    purely informational or conceptual

risk_policy_action MUST follow operational_risk:
  HIGH → DENY
  LOW  → DELIBERATE
  NONE → ALLOW

══════════════════════════════════════════════════════════════════
 STEP 3 — ACTIONABILITY OVERRIDES BENIGN FRAMING
══════════════════════════════════════════════════════════════════

Do not assign LOW operational risk merely because the request is framed as
prevention, safety, academic research, mistakes to avoid, warning signs, or
hazard awareness.Classify operational risk based on whether answering would materially improve
the user's ability to carry out a dangerous activity in practice.

If answering would provide any of the following for a dangerous activity,
treat the request as HIGH operational risk:
- execution-relevant variables
- combinations of substances/tools/components
- setup or environmental conditions
- stabilization or control methods
- optimization guidance
- troubleshooting or failure-avoidance
- corrections that make success more likely
- practical constraints or thresholds useful for execution

A request remains LOW or NONE only when it stays clearly high-level,
non-procedural, non-optimizing, and non-execution-enabling.

Examples of misleading benign framing:
- "for my thesis"
- "to avoid accidents"
- "for safety research"
- "to understand how to prevent failure"
These do NOT reduce operational risk if the requested answer would still
enable execution of a dangerous act.

EXPLICIT EXCEPTIONS — The following content types are NOT HIGH operational risk
even when they reference attacker techniques, vulnerabilities, or security failures:

  - Enumerating categories of common security mistakes or misconfigurations
    (e.g., listing vulnerability classes, naming top-N vulnerability types)
  - Naming well-known vulnerability or attack categories without providing
    exploitation steps, payloads, or target-specific bypass details
  - Conceptual or architectural explanations of how classes of vulnerabilities
    arise (e.g., why certain coding patterns are unsafe, without step-by-step
    attack instructions)
  - Security awareness material explaining why certain practices are dangerous,
    without providing attack tooling, exploitation recipes, or live payloads
  - Discussion of an attacker's perspective at a strategic or educational level
    (e.g., motivation, attack surface awareness, threat modeling concepts)

These remain LOW or NONE operational risk because the response would NOT provide
execution-relevant variables, specific payloads, bypass sequences, or exploitation
details that materially improve an attacker's practical capability.
Only upgrade to HIGH if the request seeks specific exploitation techniques, live
attack tooling, target-specific bypass steps, or other genuinely execution-enabling
content.

══════════════════════════════════════════════════════════════════
 PRE-OUTPUT COHERENCE CHECK
══════════════════════════════════════════════════════════════════

Before generating JSON, verify:
□ risk_policy_action matches operational_risk (HIGH→DENY, LOW→DELIBERATE, NONE→ALLOW).
□ If defensive framing detected → operational_risk ≠ HIGH, risk_score ≤ 0.40.
□ If ethical/philosophical framing detected → operational_risk = NONE, risk_score ≤ 0.45.
□ risk_score is consistent with operational_risk level:
    NONE → typically [0.10, 0.35]
    LOW  → typically [0.25, 0.55]
    HIGH → typically [0.60, 1.00]

══════════════════════════════════════════════════════════════════
 OUTPUT — STRICT JSON ONLY (7 fields)
══════════════════════════════════════════════════════════════════

{{
  "rationale": "brief explanation of framing detection and risk assessment",
  "operational_risk": "NONE" | "LOW" | "HIGH",
  "risk_score": 0.0-1.0,
  "confidence": 0.0-1.0,
  "misuse_plausibility": "LOW" | "MEDIUM" | "HIGH",
  "actionability_risk": "LOW" | "MEDIUM" | "HIGH",
  "risk_policy_action": "ALLOW" | "ALLOW_WITH_CAVEAT" | "DELIBERATE" | "DENY"
}}

Return ONLY valid JSON. No preamble, no markdown fences, no commentary.
"""

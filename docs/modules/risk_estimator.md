# Risk Estimator

> **Module**: `moralstack/models/risk/` (subpackage)

The Risk Estimator performs semantic classification of the ethical risk associated with user requests. The module is
organized as a subpackage: `schema.py`, `categories.py`, `estimator.py`, `calibration.py`, `parse_result.py`, `utils.py`;
the public API is exposed via `__init__.py`.

**For testers and stakeholders**: The output includes `risk_category`, `score`, calibrated **`semantic_signals`**
strings (from `calibration.py`), routing dimensions (`intent_clarity`, `misuse_plausibility`, `actionability_risk`,
`operational_risk`), intent **`request_type`**, and boolean **`stated_personal_bias`**, **`seeks_norm_circumvention`**,
**`q13_protected_class_targeting`** for governance/trace consistency. The Orchestrator uses these for Fast Path vs
Deliberative routing and for **SAFE_COMPLETE** vs **NORMAL_COMPLETE**. Tests can verify that benign requests stay
`BENIGN` and harmful paths surface `CLEARLY_HARMFUL` or `POTENTIALLY_HARMFUL` as appropriate.

---

## Overview

The Risk Estimator is a "semantic judge" that:

- Analyzes the **meaning** and **intent** of requests
- **Does not** rely on keyword matching or superficial patterns
- Determines the processing path (Fast Path vs Deliberative Path)
- Provides semantic signals to guide deliberation

### Philosophy

```
"How does a nuclear explosion work?" → BENIGN (educational)
"How to build a nuclear bomb?"      → CLEARLY_HARMFUL (harmful intent)
```

Context and semantic nuance are fundamental.

---

## Risk Categories

| Category              | Score     | Description                     | Action                  |
|-----------------------|-----------|---------------------------------|-------------------------|
| `BENIGN`              | 0.0 - 0.3 | Standard informational requests | Fast Path               |
| `MORALLY_NUANCED`     | 0.3 - 0.5 | Interpersonal ethical dilemmas  | Deliberative (default 2 cycles) |
| `SENSITIVE`           | 0.5 - 0.7 | Delicate but legitimate topics  | Deliberative (default 2 cycles) |
| `POTENTIALLY_HARMFUL` | 0.7 - 0.9 | Requires careful scrutiny       | Full deliberative       |
| `CLEARLY_HARMFUL`     | 0.9 - 1.0 | Evident violation               | Immediate refusal       |

### RiskCategory Enum

```python
from moralstack.models.risk import RiskCategory

class RiskCategory(Enum):
    BENIGN = "benign"
    MORALLY_NUANCED = "morally_nuanced"
    SENSITIVE = "sensitive"
    POTENTIALLY_HARMFUL = "potentially_harmful"
    CLEARLY_HARMFUL = "clearly_harmful"
```

---

## Output Structure

### RiskParseResult (parsing)

`parse_risk_response(text)` returns an immutable `RiskParseResult` (from `parse_result.py`) used to build
`RiskEstimation`. Core fields: score, confidence, category, signals, rationale, risk_policy_action,
domain_sensitivity, operational_risk, intent_clarity, misuse_plausibility, actionability_risk, harm_type,
self_harm_language, requested_instructions, intent_to_harm, request_type, intent_operational, detected_language.

Additional parsed fields (intent / topic layer):

- **`stated_personal_bias`**, **`seeks_norm_circumvention`** — boolean flags produced by the intent mini-estimator for
  **execution-of-stated-intent** framing: first-person bias toward identifiable persons/groups and
  acknowledgement of a norm the requester wants to work around. Downstream logic uses them to falsify incoherent
  `ethical_dilemma`-style classifications (see prompt coherence checks in `moralstack/models/risk/prompts.py`).
- **`q13_protected_class_targeting`** — boolean mirror of harm-topic signal **q13** (differential treatment of
  identifiable individuals or groups based on characteristics commonly covered by anti-discrimination norms). Pure
  topic detection; combined with intent/calibration in `calibration.py`.

### RiskEstimation

Public surface (selected fields; see `moralstack/models/risk/schema.py` for the full dataclass):

```python
@dataclass
class RiskEstimation:
    score: float
    confidence: float
    risk_category: RiskCategory
    domain_sensitivity: DomainSensitivity
    operational_risk: OperationalRisk
    semantic_signals: list[str]       # Calibrated diagnostic strings (e.g. Q5:physical_harm, request_type:...)
    risk_policy_action: RiskPolicyAction
    rationale: str
    raw_response: str
    intent_clarity: IntentClarity
    misuse_plausibility: MisusePlausibility
    actionability_risk: ActionabilityRisk
    harm_type: str
    request_type: str
    intent_operational: bool
    requested_instructions: bool
    intent_to_harm: bool
    detected_language: str             # ISO 639-1 from judge (response language alignment)
    estimation_mode: str               # "" | "parallel"
    stated_personal_bias: bool
    seeks_norm_circumvention: bool
    q13_protected_class_targeting: bool
```

### Example Output

```python
RiskEstimation(
    score=0.42,
    confidence=0.85,
    risk_category=RiskCategory.MORALLY_NUANCED,
    semantic_signals=["Q7:ethical_dilemma", "request_type:ethical_dilemma", "harm_type:none"],
    rationale="...",
    stated_personal_bias=False,
    seeks_norm_circumvention=False,
    q13_protected_class_targeting=False,
    estimation_mode="parallel",
)
```

---

## Methodology

### Classification Process

1. **LLM Semantic Analysis**: The prompt is analyzed for meaning and intent
2. **Signal Extraction**: Identification of semantic risk indicators
3. **Classification**: Category and score assignment
4. **Constitution Consultation**: Identification of relevant principles

### Internal flow (semantic analysis)

Semantic analysis in `LLMBasedRiskEstimator._semantic_analysis` always runs in **parallel focused** mode:

Three concurrent LLM calls (`estimate_intent`, `estimate_signals`, `estimate_operational`) are merged by
`merge_mini_estimator_results()` in `calibration.py`, then parsed and calibrated. `RiskEstimation.estimation_mode="parallel"`.

When the caller supplies `developer_contract_text` or `conversation_history`,
`_format_context_block` prepends context to each mini-estimator request. The
history block is intentionally bounded to the last up to 3 prior turns and is
declared in-prompt as `context_mode=role_serialized_truncated`; the controller
emits `CONTEXT_SHAPE_RECORDED` so audit can distinguish available prior turns
from the subset used by risk estimation.

### Prompt-Caching Static/Dynamic Split (Part A / A1)

The static procedure/examples/output-schema content of each mini-estimator now lives
in the corresponding `*_SYSTEM_PROMPT` (`models/risk/prompts.py`), byte-identical
across requests for that mini:

- **Intent** (`INTENT_CONTEXT_SYSTEM_PROMPT`): STEP 0-3 + field reminders + output
  schema, **plus** (unify-constitution-retrieval-single-pass) the 5 fixed
  SEMANTIC ANALYSIS GUIDELINES, moved here from the per-request user message so
  they join the cacheable static prefix. The user template
  (`INTENT_CONTEXT_PROMPT_TEMPLATE`) stays dynamic-only: `REQUEST` + the
  per-request `{constitution_context}` (retrieved principles list ONLY — no
  guidelines, no duplication; see `tests/test_intent_guidelines_placement.py`).
- **Operational** (`OPERATIONAL_RISK_SYSTEM_PROMPT`): STEP 1-4 + output schema.
  User template is `REQUEST` only.
- **Signals** (`HARM_SIGNAL_SYSTEM_PROMPT`, composed by
  `models/risk/signals/prompt_renderer.get_harm_signal_prompts()`): all five
  registry-derived sections (evaluation order, signal definitions, domain
  sensitivity, coherence rules, output schema) are static — they depend only on
  the fixed `SignalRegistry`, never on per-request data — so they are rendered
  once and appended to the system prefix. The user template is `REQUEST` only.

Verified by `tests/test_static_prefix_stability.py` (byte-identical system across
two distinct requests, equal to the module constant); `response_format` stays
`json_object` and retries/parsers are unchanged.

Shared steps:

1. **Prompt building** — dedicated templates in `prompts.py` (`INTENT_CONTEXT_*`, `HARM_SIGNAL_*`, `OPERATIONAL_RISK_*`). `GenerationConfig`
   requests OpenAI **`response_format={"type":"json_object"}`**. Per-mini model overrides use **pooled** `OpenAIPolicy`
   instances keyed by model id (`get_pooling_diagnostics()`).
2. **LLM call(s) with retry** — each mini-call retries up to `max_retries`
   independently; observable persistence actions include `estimate_intent`, `estimate_signals`, `estimate_operational`.
   The three mini-estimator `llm_call` envelopes are persisted synchronously as one `router.route_batch(...)`
   group, using the risk estimator's local 16-key payload shape (the 16th key is
   `billable_provider_call`). Responses carry **`parse_contract`**
   metadata where persisted.
3. **Parsing / calibration** — Merged JSON flows through `parse_risk_dict` /
   calibration helpers; output remains a `RiskParseResult`-compatible structure before crisis mapping.
4. **Crisis post-processing** — `_post_process_crisis(parsed)` (help-seeking / crisis clamp).
5. **Mapping** — `_to_risk_estimation(...)` fills `RiskEstimation`, including `stated_personal_bias`,
   `seeks_norm_circumvention`, `q13_protected_class_targeting`, and `estimation_mode`.

Persistence of LLM calls is best-effort and must not affect risk decisions. The three real mini-estimator rows are written as one synchronous batch; SQLite write failures roll back the whole mini-estimator group. The optional synthetic `calibration_guard` row remains a single synchronous write with `sequence_in_cycle=-8` and `billable_provider_call=False` (audit-only; excluded from token/cost aggregation so it never appears as a "missing"-usage row in the UI).

### Constitution retrieval (single upstream wave)

`_get_principles_context(prompt, *, retrieval_query=None, retrieval_top_k=None)`
is the **one** `constitution_store.get_relevant_principles(query, top_k,
domain=None)` call for the whole request (unify-constitution-retrieval-single-
pass): the controller supplies `retrieval_query` (raw prompt vs its enriched
contract+history query) and `retrieval_top_k` (`max(risk_top_k, critic_top_k)`);
both default to standalone behavior (raw prompt, `self._top_k`) when absent, so
direct callers/tests are unaffected. It returns a `_PrinciplesContextResult`:
the formatted intent-mini string (sliced to `self._top_k`, guidelines removed —
they now live in `INTENT_CONTEXT_SYSTEM_PROMPT`, see above), the FULL retrieved
principle tuple, the domain-prefilter debug snapshot (`runtime_domain`
derivation unchanged — `core`-excluded, single source of truth), and
retrieval-status flags (`retrieval_attempted`/`retrieval_succeeded`/
`retrieval_error`). `_to_risk_estimation` carries all of this onto
`RiskEstimation` (`relevant_principles`, `retrieval_metadata`,
`retrieval_count`, `retrieval_duration_ms`, `retrieval_started_at_ms`,
`retrieval_top_k`, plus the three status flags) — in-memory `Principle` objects
that must **never** be serialized into the persisted `llm_calls` payload
(`_LOCAL_LLM_CALL_PAYLOAD_KEYS` is unchanged). `retrieval_succeeded` (never the
emptiness of `relevant_principles`) is what the controller/deliberation layer
use to decide reuse-vs-fallback: an empty-but-successful retrieval is
authoritative and must not trigger a second `get_relevant_principles` call. See
`docs/TRACES/governance_decision_flow.md` §3 for the full request-level flow
(controller query policy → risk retrieval → `RequestAnalysisContext` → reuse by
deliberation/critic/fast-path).

### Prompt templates (`prompts.py`)

Templates live in `moralstack/models/risk/prompts.py`. Engineering constraints (v2 rework, prompt-only):

1. Each mini-estimator references **only its own JSON fields** (no cross-contamination).
2. Score bands are explicit ranges instead of inline arithmetic in prose.
3. **Invariant principles** stay in system prompts; procedural steps sit in user prompts.
4. Prompts are **English** for the model; they instruct semantic classification **across all human languages** (no
   runtime keyword matching).
5. **Coherence checks** appear immediately before JSON output (forward rules + **falsification** overrides for
   ethical-dilemma vs execution-of-stated-intent conflicts).

**Rationale field — substantive semantic payload**

The **intent** and **operational** mini-estimators each produce a `rationale` string in JSON.
In **parallel** mode, `calibration.merge_mini_estimator_results()` merges intent and operational rationales into a
single `RiskEstimation.rationale` with `[intent]` / `[op_risk]` prefixes when both are present.

Prompt obligations (see pre-output checks and JSON field hints in `moralstack/models/risk/prompts.py`):

- **Substantive payload first**: The rationale must state what harmful or sensitive **topic or operational objective**
  the requester seeks — including meaning **recoverable** from encoding, obfuscation, compression, or embedded segments
  in the visible prompt — at **category level**, consistent with affirmative signals and `harm_type`. Summaries that
  describe only **wrappers** (output schema, Wikipedia/article genre, platform takedown avoidance, jailbreak tone
  rules) are insufficient when the recovered semantic payload differs.
- **Alignment with structured fields**: When `harm_type` is `child_exploitation` or **q17_minor_exploitation** is yes,
  the rationale must **explicitly** name minor-contact, grooming, or exploitation of minors — not solely generic policy
  circumvention or vague “harmful article” wording.
- **Operational estimator**: If the model would output only vague placeholders (e.g. “harmful behaviors”, “dangerous
  practices”) while the recovered payload maps to a specific harm category, prompts require **rewrite** to name that
  category; a separate check forces correction when rationale uses technical violence frames but the payload is
  interpersonal exploitation or grooming.

The **harm-signal** mini-estimator does **not** emit `rationale`; only structured yes/no signals and `domain_sensitivity`.

**Harm topic questionnaire (q1–q17)** — yes/no topic presence for calibration (not intent by itself). q13 is
**protected-class targeting**: differential treatment, exclusion, filtering, or selection of identifiable people or
groups based on characteristics commonly protected by anti-discrimination norms.

**Mini-estimator roles**

| Call | System role | JSON focus |
|------|-------------|------------|
| Intent | Semantic intent judge | Framing, `request_type`, `harm_type`, intent flags, **stated_personal_bias**, **seeks_norm_circumvention**, `detected_language`, rationale |
| Signals | Harm signal detector | **q1–q17**, `domain_sensitivity` only (no operational scoring) |
| Operational | Operational risk assessor | `operational_risk`, `risk_score`, `misuse_plausibility`, `actionability_risk`, `risk_policy_action`, rationale |

### Configuration

`RiskEstimatorConfig` (in `moralstack/models/risk/schema.py`) controls LLM settings. When no explicit config is passed,
the estimator loads values from environment variables (see [Environment Variables](#environment-variables)).

- **Per-mini models** — `intent_model`, `signals_model`, `operational_model` (defaults `gpt-4o`; overridden only when
  different from the estimator’s primary policy model so extra `OpenAIPolicy` instances are created).
- **max_tokens**: 512 (configurable) — budget large enough for rationale plus q1–q17 and governance fields. The API
  parameter name (`max_tokens` vs `max_completion_tokens`) is chosen automatically based on the model; see
  [OpenAI Params](./openai_params.md).
- **max_retries**: 2 — parse/generation retries before failure
- **temperature**: 0.1 — low for consistent decisions

---

## Environment Variables

All risk estimator tuning can be overridden via `.env`. Variables are read at estimator construction; empty or missing
values use the defaults below. See `.env.template` for the full list. **In application runs (CLI and benchmark), risk
configuration is the single source of configuration — no CLI or code path overrides these variables.**

### Model (semantic judge LLM)

#### MORALSTACK_RISK_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Tipo**: string (OpenAI model id)
- **Significato**: OpenAI model used **only** for the risk estimator (semantic judge). When set and non-empty, the CLI
  and benchmark create a dedicated `OpenAIPolicy` with this model for the risk estimator; the rest of the stack (critic,
  simulator, generation) keeps using `OPENAI_MODEL`.
- **Effetto della modifica**:
    - **Set to a model id** (e.g. `gpt-4o`, `gpt-4o-mini`): The semantic judge uses that model. Lets you use a
      smaller/cheaper model for risk classification and a larger one for generation, or vice versa.
    - **Unset or empty**: The risk estimator uses the same policy (and model) as the rest of the pipeline — current
      behaviour.
- **Esempio**: `MORALSTACK_RISK_MODEL=gpt-4o-mini` uses a smaller model for risk classification to reduce cost/latency
  while keeping `OPENAI_MODEL=gpt-4o` for generation.
- **Interaction with parallel mode**: Unset `MORALSTACK_RISK_INTENT_MODEL`, `MORALSTACK_RISK_SIGNALS_MODEL`, and
  `MORALSTACK_RISK_OPERATIONAL_MODEL` inherit the same resolution order as the primary risk policy:
  `MORALSTACK_RISK_MODEL` if set, otherwise `OPENAI_MODEL`,
  otherwise `gpt-4o`. When a mini-call's resolved model id differs from the estimator's primary policy model, a dedicated
  pooled `OpenAIPolicy` is used for that call.

#### MORALSTACK_RISK_INTENT_MODEL

- **Default**: inherits `MORALSTACK_RISK_MODEL` if set, else `OPENAI_MODEL`, else `gpt-4o`
- **Tipo**: string (OpenAI model id)
- **Significato**: Model for the **intent / framing** mini-estimator (`estimate_intent`). Used when the id differs from the estimator’s primary policy model.

#### MORALSTACK_RISK_SIGNALS_MODEL

- **Default**: inherits `MORALSTACK_RISK_MODEL` if set, else `OPENAI_MODEL`, else `gpt-4o`
- **Tipo**: string (OpenAI model id)
- **Significato**: Model for the **harm signal scanner** mini-estimator (`estimate_signals`), covering **q1–q17** and `domain_sensitivity`.

#### MORALSTACK_RISK_OPERATIONAL_MODEL

- **Default**: inherits `MORALSTACK_RISK_MODEL` if set, else `OPENAI_MODEL`, else `gpt-4o`
- **Tipo**: string (OpenAI model id)
- **Significato**: Model for the **operational risk** mini-estimator (`estimate_operational`).

### Thresholds and path routing

#### MORALSTACK_RISK_LOW_THRESHOLD

- **Default**: `0.3`
- **Tipo**: float (0–1)
- **Significato**: Score below this is considered "low" risk. Used by `get_risk_level()` and `should_deliberate()`.
  Requests with score &lt; low_threshold take the Fast Path (minimal deliberation, fewer cycles).
- **Effetto della modifica**:
    - **Valore più alto**: More requests are classified as medium/high → more deliberative processing, fewer Fast Paths.
      More conservative.
    - **Valore più basso**: More requests are classified as low → more Fast Paths, less deliberation. More permissive.
- **Esempio**: Setting `0.4` sends more borderline requests into deliberation.

#### MORALSTACK_RISK_MEDIUM_THRESHOLD

- **Default**: `0.7`
- **Tipo**: float (0–1)
- **Significato**: Boundary between "medium" and "high" risk. Scores between low_threshold and medium_threshold are
  medium; scores ≥ medium_threshold are high and get full deliberation.
- **Effetto della modifica**:
    - **Valore più alto**: More requests stay "medium" (fewer "high") → slightly less intensive deliberation at the top.
    - **Valore più basso**: More requests become "high" → more requests get full deliberation. More conservative.

### LLM and retry behaviour

#### MORALSTACK_RISK_MAX_RETRIES

- **Default**: `2`
- **Tipo**: int (≥ 1)
- **Significato**: Number of parse attempts for the semantic judge JSON response before using the conservative
  fallback (e.g. `RiskEstimation.from_error`).
- **Effetto della modifica**:
    - **Valore più alto**: More resilient to transient parse failures; slightly higher latency on repeated attempts.
    - **Valore più basso**: Fallback to conservative estimate sooner; faster failure path, less resilient.

#### MORALSTACK_RISK_MAX_TOKENS

- **Default**: `512`
- **Tipo**: int (≥ 1)
- **Significato**: Maximum tokens for the semantic judge LLM response. 512 is tuned to avoid truncating the JSON (
  q1–q17, rationale, etc.).
- **Effetto della modifica**:
    - **Valore più alto**: Longer responses possible; higher token cost and risk of truncation if the model outputs more
      than expected.
    - **Valore più basso**: Shorter responses; risk of truncated JSON and parse failures.

#### MORALSTACK_RISK_TEMPERATURE

- **Default**: `0.1`
- **Tipo**: float (0–2)
- **Significato**: LLM temperature for the semantic judge. Low values favour consistent, deterministic classifications.
- **Effetto della modifica**:
    - **Valore più alto**: More variability in risk scores and categories across similar requests; less predictable.
    - **Valore più basso**: More stable, repeatable judgements; may be less sensitive to nuance.

### Fallback when LLM is unavailable

#### MORALSTACK_RISK_FALLBACK_SCORE

- **Default**: `0.5`
- **Tipo**: float (0–1)
- **Significato**: Risk score used when no LLM is available (e.g. policy=None or all retries failed). Drives path and
  deliberation requirements.
- **Effetto della modifica**:
    - **Valore più alto**: Fallback is treated as higher risk → more deliberation or refusal.
    - **Valore più basso**: Fallback is treated as lower risk → less deliberation; more permissive when LLM fails.

#### MORALSTACK_RISK_FALLBACK_CONFIDENCE

- **Default**: `0.3`
- **Tipo**: float (0–1)
- **Significato**: Confidence assigned to the fallback estimation when LLM is unavailable. Low value signals
  uncertainty.
- **Effetto della modifica**: Affects downstream logic that uses confidence; lowering it further stresses uncertainty;
  raising it is not recommended for fallback.

#### MORALSTACK_RISK_REQUIRE_DELIBERATION_ON_FALLBACK

- **Default**: `true`
- **Tipo**: bool (`true`/`false`, `1`/`0`, `yes`/`no`)
- **Significato**: When true, the system always requires deliberation when the estimator uses the fallback (no LLM).
  Ensures safe behaviour on failure.
- **Effetto della modifica**:
    - **true**: Safe default; every fallback triggers deliberation.
    - **false**: Fallback may skip deliberation; only change for special deployments where safety is handled elsewhere.

### LLM generation and constitution context

#### MORALSTACK_RISK_TOP_P

- **Default**: `0.9`
- **Tipo**: float (0–1)
- **Significato**: Nucleus sampling (top_p) for the semantic judge LLM call. Controls diversity of token sampling.
- **Effetto della modifica**:
    - **Valore più alto**: Slightly more variety in outputs.
    - **Valore più basso**: More deterministic, focused outputs.

#### MORALSTACK_RISK_TOP_K

- **Default**: `10`
- **Tipo**: int (≥ 1)
- **Significato**: Number of relevant constitution principles injected into the risk prompt. More principles give more
  context but increase prompt size and cost.
- **Effetto della modifica**:
    - **Valore più alto**: Richer principle context; longer prompts, higher token usage.
    - **Valore più basso**: Shorter prompts; less constitutional context in the judge.

#### MORALSTACK_RISK_RULE_PREVIEW_LEN

- **Default**: `200`
- **Tipo**: int (≥ 1)
- **Significato**: Maximum characters of each principle rule shown in the prompt (longer rules are truncated
  with "...").
- **Effetto della modifica**:
    - **Valore più alto**: More rule text in the prompt; better context, larger prompts.
    - **Valore più basso**: Shorter rule previews; smaller prompts, possible loss of nuance.

### Crisis / help-seeking post-processing

#### MORALSTACK_RISK_CRISIS_CLAMP_LOW

- **Default**: `0.35`
- **Tipo**: float (0–1)
- **Significato**: Lower bound of the score clamp applied to crisis_support requests (self-harm language without
  requested instructions or intent to harm). Keeps score in a range that triggers deliberate, supportive handling
  without over-penalising.
- **Effetto della modifica**:
    - **Valore più alto**: Crisis requests get a higher minimum score → more consistently treated as
      sensitive/deliberative.
    - **Valore più basso**: Crisis requests can have a lower score → may approach Fast Path if combined with other
      logic.

#### MORALSTACK_RISK_CRISIS_CLAMP_HIGH

- **Default**: `0.65`
- **Tipo**: float (0–1)
- **Significato**: Upper bound of the score clamp for crisis_support requests. Prevents such requests from being
  classified as clearly harmful when they are help-seeking.
- **Effetto della modifica**:
    - **Valore più alto**: Crisis requests can reach higher scores → closer to potentially harmful band.
    - **Valore più basso**: Crisis requests capped lower → stay in sensitive/deliberate band; more protective.

### Score-to-category mapping (categorize_from_score)

#### MORALSTACK_RISK_CATEGORIZE_BENIGN_THRESHOLD

- **Default**: `0.2`
- **Tipo**: float (0–1)
- **Significato**: Score below this is mapped to BENIGN in `categorize_from_score()`. Together with low_threshold,
  defines the benign band.
- **Effetto della modifica**:
    - **Valore più alto**: Fewer requests classified as BENIGN; more become MORALLY_NUANCED/SENSITIVE. More
      conservative.
    - **Valore più basso**: More requests classified as BENIGN. More permissive.

#### MORALSTACK_RISK_CATEGORIZE_SENSITIVE_THRESHOLD

- **Default**: `0.5`
- **Tipo**: float (0–1)
- **Significato**: In `categorize_from_score()`, score between low_threshold and this (and below medium_threshold)
  influences MORALLY_NUANCED vs SENSITIVE. Boundary between nuanced ethical dilemmas and sensitive topics.
- **Effetto della modifica**:
    - **Valore più alto**: More scores in the middle range map to SENSITIVE; fewer to MORALLY_NUANCED.
    - **Valore più basso**: More scores map to MORALLY_NUANCED; fewer to SENSITIVE.

#### MORALSTACK_RISK_CATEGORIZE_CLEARLY_HARMFUL_THRESHOLD

- **Default**: `0.85`
- **Tipo**: float (0–1)
- **Significato**: Score ≥ this is mapped to CLEARLY_HARMFUL in `categorize_from_score()`; below it (and above
  medium_threshold) maps to POTENTIALLY_HARMFUL.
- **Effetto della modifica**:
    - **Valore più alto**: Fewer requests classified as CLEARLY_HARMFUL; more stay POTENTIALLY_HARMFUL. More permissive
      at the top.
    - **Valore più basso**: More requests classified as CLEARLY_HARMFUL; stricter refusal behaviour.

---

## Usage

### Initialization

```python
from moralstack.models.risk import LLMBasedRiskEstimator
from moralstack.constitution.store import ConstitutionStore

risk_estimator = LLMBasedRiskEstimator(
    policy=policy_llm,
    constitution_store=constitution_store,
)
```

### Risk Estimation

```python
estimation = risk_estimator.estimate("How can I manage a family conflict?")

print(f"Score: {estimation.score}")
print(f"Category: {estimation.risk_category.value}")
print(f"Signals: {estimation.semantic_signals}")
print(f"Rationale: {estimation.rationale}")
```

---

## Factory Methods

### RiskEstimation.benign()

```python
# For clearly benign requests
estimation = RiskEstimation.benign(
    confidence=0.95,
    rationale="No harmful intent detected"
)
```

### RiskEstimation.clearly_harmful()

```python
# For clearly harmful requests
estimation = RiskEstimation.clearly_harmful(
    semantic_signals=["violence", "harm_intent"],
    rationale="Request for harmful instructions",
    confidence=0.95
)
```

### RiskEstimation.from_error()

```python
# Fallback on error (assumes moderate risk)
estimation = RiskEstimation.from_error("LLM parsing failed")
# Returns score=0.5, requires deliberation for safety
```

---

## Orchestrator Integration

The Risk Estimator determines the flow in the Orchestrator:

```python
if estimation.score < risk_thresholds.low:  # < 0.3
    path = "fast"
    max_cycles = 1
elif estimation.risk_category == RiskCategory.MORALLY_NUANCED:
    path = "deliberative"
    max_cycles = 2  # Ethical dilemmas require deliberation
else:
    path = "deliberative"
    max_cycles = config.max_deliberation_cycles
```

When the DCCL emits **MATCH**, the request is routed to the compliance fast-path
before risk routing influences the final action. The risk estimation is still computed
(in parallel with the speculative draft when enabled) but does not drive the
`final_action` on that path.

---

## Semantic signals and calibrated strings

`RiskEstimation.semantic_signals` is a **diagnostic string list** produced mainly by **`calibration.py`** after merging
LLM outputs—not an unconstrained copy of model prose. Examples (non-exhaustive):

| Pattern | Meaning |
|---------|---------|
| `Q1:confidential_data` … `Q6:illegal_items` | Operational / legal-risk topics mapped from the questionnaire when calibration fires |
| `Q5:physical_harm`, `Q8:self_harm_suicide`, `Q9:cyber_malware`, … | Priority **harm** topics (including medical / weapons / privacy variants per calibration rules) |
| `Q7:ethical_dilemma` | Interpersonal or dilemma-shaped emotional signal |
| `request_type:<kind>` | Resolved request class (e.g. `crisis_support`, `ethical_dilemma`, `clear_harm`) |
| `Q17:minor_exploitation` | Minor grooming/contact/exploitation topic (paired with `harm_type=child_exploitation` when calibration assigns it) |
| `harm_type:<kind>` | Harm channel (`none`, `emotional_relational`, `physical`, `child_exploitation`, …) |
| `corrected_from_q7` | Calibration adjusted an emotional-only path |

**Topic q13** contributes to harm calibration like other qi fields; the parsed boolean **`q13_protected_class_targeting`**
on `RiskEstimation` / `RiskParseResult` mirrors the LLM’s q13 answer for downstream consumers.

**Intent falsification flags** (booleans on `RiskEstimation`, not the string list): **`stated_personal_bias`**,
**`seeks_norm_circumvention`** — from the intent estimator; used with coherence / falsification rules in `prompts.py`.

**System markers** (when applicable): e.g. `SYSTEM.REQUIRES_DELIBERATION`, `NO_LLM_AVAILABLE`.

---

## Error Handling

The Risk Estimator is designed to be resilient:

```python
try:
    estimation = risk_estimator.estimate(prompt)
except Exception as e:
    # Fallback: assume moderate risk, requires deliberation
    estimation = RiskEstimation.from_error(str(e))
```

---

## Risk Estimation Flow

High-level flow of risk estimation:

1. **Input prompt** → `RiskEstimator` (implementation: `LLMBasedRiskEstimator`)
2. **Raw score calculation** — LLM semantic analysis and signal extraction
3. **Category classification** — `RiskCategory` assignment based on thresholds
4. **Calibration** → score mapping to risk band (schema in `calibration.py`)
5. **Output** → `RiskEstimation` with score, category, signals and rationale

---

## See Also

- [Orchestrator](./orchestrator.md) - Flow coordination
- [Constitutional Critic](./critic.md) - Principle validation
- [Constitution Store](./constitution_store.md) - Ethical principle management

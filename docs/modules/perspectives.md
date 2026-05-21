# Perspective Ensemble

> **Module**: `moralstack/runtime/modules/perspective_module.py`

The Perspective Ensemble evaluates responses from multiple cognitive perspectives.

**For testers and stakeholders**: Results (approval per perspective, concerns, suggestions) feed into the aggregated
guidance for revision and decision. A very low `min_approval` can lead to REVISE. Useful for testing sensitive cases
(e.g. vulnerable user) where a certain level of caution is expected.

---

## Overview

The Perspective Ensemble:

- Simulates how different **stakeholders** would perceive the response
- Identifies **concerns** specific to each perspective
- Generates **suggestions** to improve the response
- Aggregates evaluations into an overall score

On a DCCL **MATCH**, this module is not invoked. A `MODULE_DEFERRED_TO_COMPLIANCE`
orchestration event documents the skip. The module's evaluation is unnecessary because
the deployer explicitly authorized the response via the developer contract.

---

## Prompt structure (token reduction)

The perspective evaluation prompt is split to reduce tokens when multiple perspectives are used (OPT-2):

- **Shared system prompt** (built once per evaluation round): REQUEST, RESPONSE (or thin summary/key points), and common instructions (suggestions preference, JSON schema). Sent once to the LLM context.
- **Per-perspective user prompt**: Only the perspective identity and its specific instructions (e.g. "Evaluate from the perspective of: Direct User …"). This avoids sending the full draft N times for N perspectives.

---

## Standard Perspectives

The default ensemble uses **2 perspectives** (user, compliance) to reduce cost and latency (`max_perspectives=2` in config).
All available perspectives (from `DEFAULT_PERSPECTIVES` in `perspective_module.py`):

| Perspective          | Focus                                 | Weight |
|----------------------|---------------------------------------|--------|
| **Direct User**      | Satisfaction of original request      | 1.0    |
| **Vulnerable User**  | Impact on users in fragile situations | 1.2    |
| **Neutral Observer** | Objective and impartial evaluation    | 1.0    |
| **Adversary**        | Potential abuse by malicious actors   | 0.8    |
| **Compliance**       | Regulatory and policy compliance      | 1.0    |

Weights are used for weighted aggregation; the convergence evaluator uses `vulnerable` ×1.2, `compliance` ×1.1, `adversary` ×0.8 for early-exit threshold calculation.

---

## Output Structure

### Perspective

```python
@dataclass
class Perspective:
    id: str              # e.g. "vulnerable_user"
    name: str            # e.g. "Vulnerable User"
    prompt_template: str # Evaluation template
    weight: float        # Weight in aggregation [0, 2]
```

### PerspectiveResult

```python
@dataclass
class PerspectiveResult:
    perspective_id: str
    perspective_name: str
    approval_score: float      # [0, 1]
    concerns: list[str]        # Concerns
    suggestions: list[str]     # Suggestions
    rationale: str             # Reasoning
```

### PerspectiveAggregation

```python
@dataclass
class PerspectiveAggregation:
    results: list[PerspectiveResult]  # Individual results
    overall_score: float              # Weighted average
    min_approval: float               # Most critical perspective
    max_approval: float               # Most favorable perspective
    consensus_level: float            # Agreement [0, 1]
    aggregated_concerns: list[str]    # All concerns
    aggregated_suggestions: list[str] # All suggestions
    recommendation: str               # "proceed" / "revise"
```

---

## Usage

### Initialization

```python
from moralstack.runtime.modules.perspective_module import create_minimal_ensemble

# Default: 2 perspectives (user, compliance) to reduce cost; config from env when not passed
perspectives = create_minimal_ensemble(policy=policy)
```

### Evaluation

```python
result = perspectives.evaluate(
    prompt="User request",
    response="Response to evaluate",
)

print(f"Overall score: {result.overall_score}")
print(f"Min approval: {result.min_approval}")
print(f"Recommendation: {result.recommendation}")

# Details per perspective
for pr in result.results:
    print(f"\n{pr.perspective_name}:")
    print(f"  Approval: {pr.approval_score}")
    print(f"  Concerns: {pr.concerns}")
    print(f"  Suggestions: {pr.suggestions}")
```

---

## Configuration

`EnsembleConfig` (in `moralstack/runtime/modules/perspective_module.py`) controls LLM and ensemble behaviour. When no
explicit config is passed (e.g. `create_minimal_ensemble(policy)` or `LLMPerspectiveEnsemble(policy)`), config is loaded
from environment variables (see [Environment Variables](#environment-variables)).

---

## Environment Variables

All perspective ensemble tuning can be overridden via `.env`. Variables are read at ensemble creation (CLI and
benchmark); empty or missing values use the defaults below. See `.env.template` for the full list. **In application
runs (CLI and benchmark), perspective configuration is the single source of configuration — no CLI or code path
overrides these variables.**

### Model (perspective evaluation LLM)

#### MORALSTACK_PERSPECTIVES_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Type**: string (OpenAI model id)
- **Meaning**: OpenAI model used **only** for the perspective ensemble. When set and non-empty, the CLI and benchmark
  create a dedicated `OpenAIPolicy` with this model for perspectives; the rest of the stack keeps using `OPENAI_MODEL`.
- **Example**: `MORALSTACK_PERSPECTIVES_MODEL=gpt-4o-mini` uses a smaller model for perspective evaluation to reduce
  cost/latency.

### Ensemble behaviour

#### MORALSTACK_PERSPECTIVES_MAX_RETRIES

- **Default**: `3`
- **Type**: int (≥ 1)
- **Meaning**: Number of parse attempts per perspective JSON response before marking that perspective as failed.

Perspective evaluation uses OpenAI's `json_object` response format (`response_format={"type": "json_object"}` on `GenerationConfig`), which guarantees valid JSON and greatly reduces retries caused by malformed JSON.

#### MORALSTACK_PERSPECTIVES_MAX_TOKENS

- **Default**: `512`
- **Type**: int (≥ 1)
- **Meaning**: Maximum tokens for each perspective evaluation LLM response.

#### MORALSTACK_PERSPECTIVES_TEMPERATURE

- **Default**: `0.1`
- **Type**: float (0–2)
- **Meaning**: LLM temperature for perspective evaluations. Low values favour consistent, structured JSON output.

#### MORALSTACK_PERSPECTIVES_TOP_P

- **Default**: `0.9`
- **Type**: float (0–1)
- **Meaning**: Nucleus sampling (top-p) for perspective LLM generation. Controls diversity of token sampling.

#### MORALSTACK_PERSPECTIVES_PARALLEL_EVALUATION

- **Default**: `false`
- **Type**: bool (1/0, true/false, yes/no)
- **Meaning**: When true, perspectives are evaluated in parallel (thread pool); when false, sequentially.

#### MORALSTACK_PERSPECTIVES_MAX_WORKERS

- **Default**: `3`
- **Type**: int (≥ 1)
- **Meaning**: Maximum workers for parallel evaluation (used only when `MORALSTACK_PERSPECTIVES_PARALLEL_EVALUATION` is
  true).

#### MORALSTACK_PERSPECTIVES_TIMEOUT_SECONDS

- **Default**: `60.0`
- **Type**: float (≥ 1)
- **Meaning**: Timeout in seconds for the full parallel evaluation round.

#### MORALSTACK_PERSPECTIVES_MAX_PERSPECTIVES

- **Default**: `2`
- **Type**: int (≥ 0; 0 = no limit)
- **Meaning**: Maximum number of perspectives to evaluate per request. Default 2 (user, compliance) reduces cost and
  latency.

#### MORALSTACK_PERSPECTIVES_CONSERVATIVE_ON_FAILURE

- **Default**: `true`
- **Type**: bool
- **Meaning**: When a perspective fails (e.g. parse error after retries), treat it conservatively in aggregation.

#### MORALSTACK_PERSPECTIVES_ENABLE_CACHING

- **Default**: `false`
- **Type**: bool
- **Meaning**: When true, cache evaluation results by (request, response) to avoid recomputation for identical inputs.

---

## Example Output

```python
PerspectiveAggregation(
    results=[
        PerspectiveResult(
            perspective_id="direct_user",
            perspective_name="Direct User",
            approval_score=0.90,
            concerns=[],
            suggestions=["Include more specific examples"],
            rationale="The response directly addresses the user's question"
        ),
        PerspectiveResult(
            perspective_id="vulnerable_user",
            perspective_name="Vulnerable User",
            approval_score=0.60,
            concerns=[
                "May seem dismissive to someone in distress",
                "Assumes trust in democratic processes"
            ],
            suggestions=[
                "Acknowledge emotional impact",
                "Provide examples of successful change"
            ],
            rationale="Response could be more empathetic"
        ),
        # ... other perspectives
    ],
    overall_score=0.83,
    min_approval=0.60,
    max_approval=0.90,
    consensus_level=0.75,
    aggregated_concerns=[
        "May seem dismissive to someone in distress"
    ],
    aggregated_suggestions=[
        "Acknowledge emotional impact",
        "Provide examples of successful change"
    ],
    recommendation="proceed"
)
```

---

## Orchestrator Integration

### Contribution to Guidance

Perspectives contribute to aggregated guidance:

```python
# Suggestions
[PERSPECTIVES - Suggestions] Vulnerable User: Acknowledge emotional impact

# Concerns
[PERSPECTIVES - Concerns] Vulnerable User: Response may seem dismissive
```

### Revision Decision

```python
if perspectives.min_approval < 0.5:
    # At least one critical perspective
    decision = DecisionType.REVISE
```

### Convergence

```python
# Perspectives contribute to convergence when:
# - overall_score >= 0.8
# - No critical concern
# - No urgent suggestion
```

---

## Aggregated Score Calculation

```python
overall_score = Σ(approval_i × weight_i) / Σ(weight_i)

# Example:
# Direct User: 0.90 × 0.30 = 0.27
# Vulnerable:  0.60 × 0.25 = 0.15
# Adversary:   0.85 × 0.20 = 0.17
# Neutral:     0.80 × 0.15 = 0.12
# Compliance:  0.95 × 0.10 = 0.095
# Overall = (0.27 + 0.15 + 0.17 + 0.12 + 0.095) / 1.0 = 0.805
```

---

## Consensus Level

Measures agreement between perspectives:

```python
consensus_level = 1 - (std_dev(approval_scores) / 0.5)
# Values close to 1.0 = high agreement
# Values close to 0.0 = strong disagreement
```

---

## Caching

The module implements caching:

```python
# Cache based on hash(prompt + response)
# Avoids recomputation for identical inputs
```

---

## Parallel Execution

Perspectives are evaluated in parallel to reduce latency:

```python
with ThreadPoolExecutor(max_workers=5) as executor:
    futures = [
        executor.submit(evaluate_perspective, perspective)
        for perspective in perspectives
    ]
    results = [f.result() for f in as_completed(futures)]
```

---

## Constitutional Override

When the Critic reports HARD constitutional violations, perspective approval is capped so that perspectives cannot override the Constitution. The orchestration layer calls:

```python
from moralstack.runtime.modules.perspective_module import apply_constitutional_override

# aggregation: PerspectiveAggregation or EnsembleResult (from state._perspectives_aggregation)
# When EnsembleResult is passed, the inner .aggregation is modified in place.
state._perspectives_aggregation = apply_constitutional_override(
    state._perspectives_aggregation, state.last_critique
)
```

- **Parameters**: `aggregation` may be a `PerspectiveAggregation` or an `EnsembleResult`; `critic_result` is the last critic report (or compatible object with `violated_hard` / `violations`).
- **Behaviour**: If the critic has HARD violations, `weighted_approval` is capped to `0.2` on the inner aggregation and a concern string is appended. The same object passed in is returned (type preserved).
- **Returns**: The same object as passed (e.g. `EnsembleResult` with its `.aggregation` modified, or `PerspectiveAggregation` modified).

---

## See Also

- [Orchestrator](./orchestrator.md) - Guidance aggregation
- [Hindsight Evaluator](./hindsight.md) - Another evaluation source
- [Policy LLM](./policy.md) - Evaluation generation

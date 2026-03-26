# Constitutional Critic

> **Module**: `moralstack/runtime/modules/critic_module.py`

The Constitutional Critic validates responses against the system's ethical constitution.

**For testers and stakeholders**: The Critic returns a structured **decision** (`PROCEED` | `REVISE` | `REFUSE`). A
`REFUSE` decision or hard violations lead the Orchestrator to **REFUSE**. Tests can verify that responses violating
hard principles produce `has_critical_violations=True` and `decision=REFUSE`.

---

## Overview

The Constitutional Critic:

- **Identifies violations** of ethical principles
- **Quantifies severity** of each violation
- **Distinguishes** between hard constraints (non-negotiable) and soft norms (flexible)
- **Generates guidance** for response revision
- **Flags information leakage** when the request warrants refusal (see below)

---

## Information Leakage

When the request warrants refusal (e.g. terrorism, weapons, critical infrastructure targeting), the critic also flags *
*information leakage**: responses that provide operationally useful information—such as listing components, targets,
materials, or structural elements that could be exploited—even if framed as "general principles" or "educational
content."

Example: A response that refuses to give step-by-step attack instructions but enumerates key structural components (e.g.
main cables, towers, anchorages of a bridge) implicitly maps to high-value targets and is flagged as a violation. Such
leakage is mapped to `CORE.DUALUSE.1` (or `CORE.NM.1` when applicable).

---

## Evaluation Process

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Response   │ ──► │   Principle  │ ──► │  LLM         │
│   + Prompt   │     │   Retrieval  │     │  Evaluation  │
└──────────────┘     └──────────────┘     └──────────────┘
                                                  │
                                                  ▼
                     ┌──────────────────────────────────┐
                     │         CriticReport             │
                     │  - violations[]                  │
                     │  - severity_score                │
                     │  - has_critical_violations       │
                     │  - revision_guidance             │
                     └──────────────────────────────────┘
```

1. **Principle Retrieval**: Identifies applicable principles via hybrid matching
2. **LLM Evaluation**: Analyzes the response against each principle
3. **Quantification**: Assigns severity score for each violation
4. **Guidance Generation**: Produces instructions for revision

---

## Output Structure

### CriticReport

```python
@dataclass
class CriticReport:
    violations: list[Violation]  # List of detected violations
    severity_score: float  # Aggregate score [0, 1]
    has_critical_violations: bool  # True if hard constraint violated (alias: violated_hard)
    decision: str  # "PROCEED" | "REVISE" | "REFUSE" (guides Orchestrator)
    revision_guidance: str  # Revision instructions
    raw_response: str  # LLM response (debug)
    parse_attempts: int  # Parsing attempts
```

### Violation

```python
@dataclass
class Violation:
    principle_id: str  # e.g. "CORE.NM.1"
    principle_title: str  # e.g. "Non-Maleficence"
    severity: float  # [0, 1]
    constraint_type: str  # "hard" or "soft"
    rationale: str  # Explanation
    evidence: str  # Citation from response
```

---

## Violation Types

### Hard Constraints

Non-negotiable violations that result in **immediate refusal**:

| Principle        | Description                  |
|------------------|------------------------------|
| `CORE.NM.1`      | Physical harm                |
| `CORE.CSAM.1`    | Child protection             |
| `CORE.NM.2`      | Illegal activity             |
| `CORE.MALWARE.1` | Malware/security             |
| `CORE.PRIV.1`    | Privacy                      |
| `CORE.DUALUSE.1` | Dual-use information leakage |

### Soft Norms

Flexible violations that result in **caveat or revision**:

| Principle           | Description           |
|---------------------|-----------------------|
| `SOFT.HONEST.1`     | Accuracy              |
| `SOFT.HELPFUL.1`    | Usefulness            |
| `SOFT.VULNERABLE.1` | Vulnerable protection |
| `SOFT.BALANCED.1`   | Perspective balance   |

---

## Severity Score Calculation

```python
severity_score = Σ(severity_i × weight_i) / Σ(weight_i)

# Weights:
# - Hard constraints: weight = 2.0
# - Soft norms: weight = 1.0
```

### Minimum Severity Filter

Violations with `severity < 0.15` are filtered to avoid false positives.

---

## Configuration

`CriticConfig` (in `moralstack/runtime/modules/critic_module.py`) controls LLM and evaluation settings. When no explicit
config is passed (e.g. `LLMConstitutionalCritic(policy, store)` or `create_critic(policy)`), config is loaded from
environment variables (see [Environment Variables](#environment-variables)).

---

## Environment Variables

All critic tuning can be overridden via `.env`. Variables are read at critic creation (CLI and benchmark); empty or
missing values use the defaults below. See `.env.template` for the full list. **In application runs (CLI and benchmark),
.env is the single source of configuration for both critic config and model — no CLI or code path overrides these
variables.**

### Model (critic evaluation LLM)

#### MORALSTACK_CRITIC_MODEL

- **Default**: *(none — uses the same model as the rest of the stack, e.g. `OPENAI_MODEL` or `gpt-4o`)*
- **Type**: string (OpenAI model id)
- **Meaning**: OpenAI model used **only** for the constitutional critic. When set and non-empty, the CLI and benchmark
  create a dedicated `OpenAIPolicy` with this model for the critic; the rest of the stack keeps using `OPENAI_MODEL`. In
  run and benchmark this is the single source for the critic model — no CLI override.
- **Example**: `MORALSTACK_CRITIC_MODEL=gpt-4o-mini` uses a smaller model for constitutional critique to reduce
  cost/latency.

### Critic behaviour

#### MORALSTACK_CRITIC_MAX_RETRIES

- **Default**: `2`
- **Type**: int (>= 1)
- **Meaning**: Number of parse attempts for the critic JSON response before raising an error.

#### MORALSTACK_CRITIC_MAX_TOKENS

- **Default**: `384`
- **Type**: int (>= 1)
- **Meaning**: Maximum tokens for the critic LLM response.

#### MORALSTACK_CRITIC_TEMPERATURE

- **Default**: `0.1`
- **Type**: float, clamped to [0.0, 2.0]
- **Meaning**: Temperature for critic LLM generation. Lower values produce more deterministic evaluations.

#### MORALSTACK_CRITIC_TOP_P

- **Default**: `0.9`
- **Type**: float, clamped to [0.0, 1.0]
- **Meaning**: Nucleus sampling (top-p) for critic LLM generation. Controls diversity of token sampling.

#### MORALSTACK_CRITIC_TOP_K_PRINCIPLES

- **Default**: `20`
- **Type**: int (>= 1)
- **Meaning**: Maximum number of constitution principles included in the critic prompt.

#### MORALSTACK_CRITIC_INCLUDE_EXAMPLES

- **Default**: `false`
- **Type**: bool (1/true/yes or 0/false/no)
- **Meaning**: Whether to include violation examples from principles in the critic prompt.

---

## Usage

### Initialization

```python
from moralstack.runtime.modules.critic_module import LLMConstitutionalCritic
from moralstack.constitution.store import ConstitutionStore

# Config from env when not passed; model from MORALSTACK_CRITIC_MODEL (or main policy)
critic = LLMConstitutionalCritic(
    policy=policy,
    store=constitution_store,
)
```

### Critique

```python
report = critic.critique(
    prompt="User request",
    response="Response to evaluate",
    domain="medical",  # Optional: activate overlay
)

if report.has_critical_violations:
    # Refuse the request
    pass
elif report.violations:
    # Revise using the guidance
    guidance = report.revision_guidance
```

### Quick Check

For the fast path, quick check of hard constraints only:

```python
result = critic.quick_check(response, hard_only=True)

if not result.passed:
    # Hard constraint violation found
    print(result.critical_violation)
```

---

## Revision Guidance

The generated guidance is structured to guide revisions. When the decision is REVISE or REFUSE, the critic **must**
include concrete safe alternatives using the format `suggest: [specific topics, resources, or reframing]`.

Examples:

```
suggest: Discuss ASLR mitigations, defensive coding, secure SDLC; avoid exploit code.
suggest: Historical/regulatory overview, ethical frameworks, specialist consultation; avoid operational details.
Include disclaimer on the informational nature of the response. suggest: Recommend consultation with a medical professional.
```

The `suggest:` prefix is parsed by the Response Assembler when formatting fallback refusals. The guidance is passed to
`policy.rewrite()` for revision or to `policy.refuse()` when assembling the final refusal.

---

## Example Output

```python
CriticReport(
    violations=[
        Violation(
            principle_id="MED.DISCLAIMER.1",
            principle_title="Medical Disclaimer",
            severity=0.7,
            constraint_type="soft",
            rationale="Absence of medical disclaimer",
            evidence="The response provides advice without specifying..."
        )
    ],
    severity_score=0.7,
    has_critical_violations=False,
    revision_guidance="Include appropriate disclaimer and recommend professional consultation"
)
```

---

## Factory Methods

### CriticReport.empty()

```python
# No violations found
report = CriticReport.empty()
```

### CriticReport.from_error()

```python
# Fallback on critical error
report = CriticReport.from_error("Parsing failed")
# Assumes worst case: severity_score=1.0, has_critical=True
```

---

## Orchestrator Integration

The Critic determines Orchestrator decisions:

```python
if report.has_critical_violations:
    decision = DecisionType.REFUSE
elif report.violations:
    decision = DecisionType.REVISE
    # Aggregate guidance for revision
else:
    decision = DecisionType.CONTINUE
```

---

## See Also

- [Constitution Store](./constitution_store.md) - Ethical principle management
- [Orchestrator](./orchestrator.md) - Flow coordination
- [Policy LLM](./policy.md) - Guided revision

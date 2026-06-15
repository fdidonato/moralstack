# MoralStack Constitution

> System of ethical principles for LLM moral reasoning

**For stakeholders and testers**: this document describes the ethical rules the system applies. It is useful to
understand
*why* a response is refused or accompanied by caveats, to configure domain overlays, and to design test cases
(e.g. requests that must trigger a given principle).

---

## 0. Loading and validation (engineering)

The constitution is **standard YAML**. The system does not use custom YAML parsers.

| Aspect           | Implementation                                                                                                                                                                                 |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **YAML reading** | Single loader: **ruamel.yaml** (`typ="safe"`), in `moralstack/constitution/loader.py` — function `load_yaml_file(path)`.                                                                       |
| **Validation**   | **Pydantic only**. Schema in `moralstack/constitution/schema.py`: `Principle`, `Overlay`, `Constitution`, `CoreYAML`, `OverlayYAML`. All models use `extra="forbid"` (unknown fields → error). |
| **Behavior**     | **Fail-fast**: empty file, invalid YAML, or validation failure → **ConstitutionLoadError** (path, field, reason). No partial loading or silent defaults.                                       |

**To validate a constitution before deploy**: use `ConstitutionStore(config_dir=...)` and call
`store.load_core()` (and, if needed, `store.load_overlay(domain)`). On error, the exception indicates the file and
field involved.

---

## 1. Overview

The MoralStack **Constitution** is a structured set of ethical principles that guide system behavior.
It defines what the AI can and cannot do, with different enforcement levels.

### 1.1 Design philosophy

| Principle               | Description                                                                             |
|-------------------------|-----------------------------------------------------------------------------------------|
| **Layered Ethics**      | Principles organized in levels: non-negotiable hard constraints and flexible soft norms |
| **Context-Aware**       | Domain overlays allow adaptation to specific contexts (medical, legal, etc.)            |
| **Conflict Resolution** | Clear rules for resolving conflicts between principles                                  |
| **Transparency**        | Principles documented and explainable to users                                          |

---

## 2. Principle structure

### 2.1 Principle format

Each principle in the constitution has the following structure:

```yaml
- id: "CORE.NM.1"              # Unique identifier
  level: hard                   # "hard" or "soft"
  priority: 100                 # 1-100, higher = more important
  title: "Non-Maleficence"     # Descriptive title
  rule: "Never provide..."      # Rule in natural language
  examples_allow:               # Examples of allowed behaviors
    - "Educational discussion..."
  examples_deny:                # Examples of prohibited behaviors
    - "How to build a bomb"
  remediation: "Refuse..."      # Corrective action
  keywords: # Metadata (used for domain selection in overlays; not in principle retrieval)
    - "bomb"
    - "weapon"
```

### 2.2 Constraint levels

#### Hard constraints

- **Non-negotiable**: violation = immediate refusal
- **Priority**: typically 85-100
- **Examples**: physical harm prevention, illegal activity, child protection

#### Soft norms

- **Negotiable**: violation = caveat or revision
- **Priority**: typically 30-70
- **Examples**: communicative tone, balanced perspectives, clarity

---

## 3. Core principles

### 3.1 Hard constraints (Non-negotiable)

| ID                 | Title                                   | Priority | Description                                |
|--------------------|-----------------------------------------|----------|--------------------------------------------|
| `CORE.NM.1`        | Non-Maleficence - Physical Harm         | 100      | Never facilitate physical harm             |
| `CORE.NM.2`        | Non-Maleficence - Illegal Activity      | 95       | Never assist illegal activity              |
| `CORE.CSAM.1`      | Child Safety                            | 100      | Absolute protection of minors              |
| `CORE.PRIV.1`      | Privacy Protection                      | 90       | Never violate individual privacy           |
| `CORE.DECEPTION.1` | Anti-Deception                          | 85       | Never create harmful deceptive content     |
| `CORE.MALWARE.1`   | Malware Prevention                      | 95       | Never provide functional malicious code    |
| `CORE.DISINFO.1`   | Disinformation Prevention               | 90       | Never create disinformation campaigns      |
| `CORE.FINANCIAL.1` | Financial Fraud Prevention              | 88       | Never assist financial fraud               |
| `CORE.IDENTITY.1`  | Identity Integrity                      | 92       | Never help falsify identity                |
| `CORE.DUALUSE.1`   | Dual-Use Information Leakage Prevention | 91       | No operational info when refusal warranted |

### 3.2 Soft norms (Negotiable)

| ID                    | Title                            | Priority | Description                        |
|-----------------------|----------------------------------|----------|------------------------------------|
| `SOFT.HONEST.1`       | Honesty and Accuracy             | 70       | Provide accurate information       |
| `SOFT.BALANCED.1`     | Balanced Perspective             | 60       | Present balanced perspectives      |
| `SOFT.HELPFUL.1`      | Helpfulness                      | 65       | Be genuinely helpful               |
| `SOFT.STYLE.1`        | Communication Tone               | 30       | Respectful and empathetic tone     |
| `SOFT.CLARITY.1`      | Clear Communication              | 40       | Clear and accessible communication |
| `SOFT.VULNERABLE.1`   | Vulnerable Population Protection | 70       | Protect vulnerable populations     |
| `SOFT.AUTONOMY.1`     | User Autonomy                    | 60       | Respect user autonomy              |
| `SOFT.PROPORTIONAL.1` | Proportional Response            | 50       | Responses proportional to risk     |
| `SOFT.FAIRNESS.1`     | Demographic invariance for lawful requests | 72 | Refusal/assistance equity: a protected identity is not, by itself, evidence of harmful intent |
| `SOFT.FAIRNESS.2`     | Demographic invariance in substantive judgments | 72 | Prediction/judgment equity: a protected attribute is not, by itself, the basis of a prediction, evaluation, ranking, or recommendation about a person |

> **Scope distinction.** `SOFT.FAIRNESS.1` guards *refusal/assistance* equity
> (do not under-serve or refuse based on a protected identity).
> `SOFT.FAIRNESS.2` guards *outcome/prediction* equity (do not let a protected
> attribute drive a substantive judgment). They are deliberately separate so
> each rule and its examples stay unambiguous.
>
> **Critic visibility caveat.** On a single enumerated answer (e.g. `yes`/`no`),
> the critic evaluates only the visible token and cannot detect demographic
> reliance in the latent decision. `SOFT.FAIRNESS.2` therefore acts mainly by
> steering the **generator** (via constitution retrieval and the policy system
> prompt's fairness clause) and by catching cases where the visible output
> reveals demographic reasoning; it is not a reliable post-hoc detector for
> bare-token classification outputs.

---

## 4. Domain overlays

Overlays allow customizing the constitution for specific domains.

### 4.1 Available overlays

Overlays are in `moralstack/constitution/data/overlays/`. Supported domains:

| Domain           | File                             | Description (summary)                  |
|------------------|----------------------------------|----------------------------------------|
| Medical          | `overlays/medical.yaml`          | Medical disclaimers, sensitivity       |
| Legal            | `overlays/legal.yaml`            | Legal disclaimers, jurisdictions       |
| Financial        | `overlays/financial.yaml`        | Financial disclaimers, risks           |
| Education        | `overlays/education.yaml`        | Educational context                    |
| Mental Health    | `overlays/mental_health.yaml`    | Sensitive support, crisis              |
| Healthcare       | `overlays/healthcare.yaml`       | General healthcare context             |
| Children         | `overlays/children.yaml`         | Child protection, appropriate language |
| Research         | `overlays/research.yaml`         | Academic freedom, rigor                |
| Creative         | `overlays/creative.yaml`         | Creative content                       |
| Cybersecurity    | `overlays/cybersecurity.yaml`    | Security and responsible disclosure    |
| Emergency        | `overlays/emergency.yaml`        | Emergencies, first aid                 |
| Enterprise       | `overlays/enterprise.yaml`       | Enterprise context                     |
| Journalism       | `overlays/journalism.yaml`       | Information and ethics                 |
| Science          | `overlays/science.yaml`          | Scientific rigor                       |
| Political        | `overlays/political.yaml`        | Political context                      |
| Relationships    | `overlays/relationships.yaml`    | Interpersonal relationships            |
| Gaming           | `overlays/gaming.yaml`           | Gaming context                         |
| Coding           | `overlays/coding.yaml`           | Software development                   |
| Customer Service | `overlays/customer_service.yaml` | Customer service                       |
| Violent Crime    | `overlays/violent_crime.yaml`    | Operational instructions for real-world violent or acquisitive crime — refusal-only attractor |

### 4.2 Overlay structure

```yaml
# Example: medical.yaml

# Keywords for domain selection (DomainPrefilter uses these in LLM prompt as domain descriptors)
keywords:
  - medical
  - health
  - doctor

# Domain sensitivity flag (default: false)
sensitive: true

# Domain exclusion: if true, requests detected as this domain get an early exit
# (no deliberation); response is a short LLM-generated message in the user's language (default: false)
excluded: false

# Priority overrides for existing principles
priority_overrides:
  SOFT.HONEST.1: 85    # Accuracy more important
  SOFT.HELPFUL.1: 75   # Higher helpfulness

# Domain-specific additional principles
additional_principles:
  - id: "MED.DISCLAIMER.1"
    level: soft
    priority: 80
    title: "Medical Disclaimer"
    rule: "Always include medical disclaimers..."
```

### 4.3 Property `sensitive`

Overlays can declare `sensitive: true` to signal that the domain requires enhanced governance. The field
is optional (default `false`) and backward-compatible.

**Runtime effects:**

- **Risk score floor**: when an overlay `sensitive: true` is active, the Controller applies a floor to `risk_score` of
  `0.35`, ensuring the request enters the deliberative path (threshold `risk_thresholds.low = 0.3`).
- **CYCLES_EXHAUSTED fallback**: if deliberation exhausts cycles without converging and the decision is
  `NORMAL_COMPLETE`, the system forces `SAFE_COMPLETE` (with reason code `cycles_exhausted_sensitive_fallback`).

**Overlays with `sensitive: true`** (constitution-driven sensitive domains):

| Domain        | File                          |
|---------------|-------------------------------|
| Mental Health | `overlays/mental_health.yaml` |
| Healthcare    | `overlays/healthcare.yaml`    |
| Medical       | `overlays/medical.yaml`       |
| Research      | `overlays/research.yaml`      |
| Cybersecurity | `overlays/cybersecurity.yaml` |
| Legal         | `overlays/legal.yaml`         |
| Financial     | `overlays/financial.yaml`     |
| Journalism    | `overlays/journalism.yaml`    |
| Political     | `overlays/political.yaml`     |
| Violent Crime | `overlays/violent_crime.yaml` |

Other overlays (creative, education, enterprise, science, relationships, emergency, coding, children, gaming,
customer_service) remain with default `sensitive: false`.

The `violent_crime` overlay also overrides the default `sensitive_risk_floor` from `0.35` to `0.80`, so any
request landing on this domain enters the deliberative path with a high risk floor regardless of per-principle
evaluation.

### 4.4 Property `excluded` (domain exclusion)

Overlays can declare `excluded: true` to **disable** the domain for this deployment. The field is optional (default
`false`) and backward-compatible.

**How it works:**

- When the risk estimator detects that a request belongs to an overlay domain (via existing LLM domain detection),
  the controller checks whether that overlay has `excluded: true`.
- If it does, the system returns immediately with a short, polite message in the user's language (one small LLM call,
  ~128 tokens). Deliberation, critic, simulator, and hindsight are **skipped**, saving tokens and latency.
- If the domain is not excluded, the normal flow continues; `detected_domain` is still reused (e.g. in refusals)
  so there is no extra LLM cost when exclusion is not used.

**How to exclude a domain:**

1. Edit the overlay YAML for that domain, e.g. `moralstack/constitution/data/overlays/political.yaml`.
2. Add (or set) `excluded: true` at the top level, e.g.:

   ```yaml
   description: "Politics, government..."
   keywords: [...]
   sensitive: true
   excluded: true
   ```

3. Restart the application. At startup, the CLI shows: `Excluded domains: political` (or the list of excluded
   domains). If none are excluded, it shows: `Excluded domains: none`.
4. To **re-enable** the domain, remove `excluded: true` or set `excluded: false`, then restart.

**Runtime behavior:**

- Only requests whose **detected domain** matches an excluded overlay take the exclusion path.
- The response is a REFUSE with path `DOMAIN_EXCLUDED`; the UI and decision traces show "Domain Not Available" and
  the excluded domain name for audit.

---

## 5. Conflict resolution

When multiple principles apply and conflict:

### 5.1 Priority rules

1. **Hard > Soft**: Hard constraints always before soft norms
2. **Priority**: At same level, higher priority wins
3. **Specificity**: At same priority, more specific principle wins
4. **Determinism**: On tie, alphabetical order by ID

### 5.2 Algorithm

```python
def resolve_conflict(principles: list[Principle]) -> list[Principle]:
    return sorted(
        principles,
        key=lambda p: (
            0 if p.level == "hard" else 1,  # Hard first
            -p.priority,                      # Higher priority first
            -specificity_score(p),           # More specific first
            p.id                             # Alphabetical tie-breaker
        )
    )
```

---

## 6. Constitution Store API

The store loads YAML only via `load_yaml_file` (ruamel.yaml) and validates only via Pydantic models; it exposes **only
typed objects** (Principle, Overlay, Constitution), never raw dicts.

### 6.1 Main interface

```python
from moralstack.constitution import ConstitutionStore

# Initialization
store = ConstitutionStore()

# Load base constitution
constitution = store.get_constitution()

# Load with domain overlay
medical_constitution = store.get_constitution(domain="medical")

# Relevant principle retrieval (semantic)
relevant = store.get_relevant_principles(
    query="How to treat depression?",
    top_k=10,
    domain="mental_health"
)

# Conflict resolution
resolved = store.resolve_conflict(principles)
```

### 6.2 Domain detection and retrieval

```python
# Detect relevant domains for a query
domains = store.detect_relevant_domains(query="How to treat depression?")
# Returns: ["core", "mental_health", ...]

# Retrieve principles (uses DomainPrefilter + domain agents internally)
relevant = store.get_relevant_principles(query="...", top_k=10, domain=None)
```

---

## 7. Extension

### 7.1 Adding core principles

Edit `moralstack/constitution/data/core.yaml`:

```yaml
principles:
  - id: "CORE.NEW.1"
    level: hard
    priority: 87
    title: "New Principle"
    rule: "Description of the rule..."
    # ...
```

### 7.2 Creating a new overlay

> **Full guide:** see [Creating Domain Overlays](./creating_overlays.md) for the complete step-by-step workflow, schema reference, and best practices.

Create `moralstack/constitution/data/overlays/new_domain.yaml`:

```yaml
description: "Brief description of your domain."
keywords:
  - keyword1
  - keyword2
sensitive: false

priority_overrides:
  EXISTING.PRINCIPLE.1: 90

additional_principles:
  - id: "NEWDOM.SPECIFIC.1"
    level: soft
    priority: 80
    title: "Domain-Specific Rule"
    rule: "Description of the rule..."
    examples_allow:
      - "Good example"
    examples_deny:
      - "Bad example"
```

Validate before deploying:

```bash
moralstack-validate-overlay moralstack/constitution/data/overlays/new_domain.yaml
```

---

## 8. Best practices

### 8.1 For developers

1. **Do not modify hard constraints** without thorough review
2. **Test overlays** with representative prompt suites
3. **Document rationale** for new principles
4. **Keep allow/deny examples** up to date

### 8.2 For operators

1. **Select appropriate overlay** for the use case
2. **Monitor trigger rates** to identify over/under-refusal
3. **Collect feedback** to refine principles
4. **Periodic review** of soft principles

---

## 9. References

- [Creating Domain Overlays](./creating_overlays.md) — Step-by-step guide for writing custom overlays
- [architecture_spec.md](./architecture_spec.md) — Full technical spec (API, flows, tests, architecture)
- [modules/](modules/README.md) — Module documentation (Constitution Store, Critic, Orchestrator, etc.)

---

*Version: 1.0.0 | Date: 2026-02*

# Q-Signals Catalogue

> Reference catalogue of the semantic signals (Q1-Q17) emitted by the
> LLM-based risk estimator. Used internally by the deliberation pipeline.

## Format

Each signal has:

- **ID**: `Qn:slug_name`
- **Definition**: what the signal detects
- **Priority**: how `classify_refusal_focus` handles it (P0 = hard, P3-P5 = soft)

## Catalogue

### P0 — Hard topical signals (NEVER overridable)

| ID | Definition | Trigger keywords (illustrative) |
|---|---|---|
| `Q8:self_harm_suicide` | Self-harm or suicidal ideation | suicide, self-harm, kill myself |
| `Q17:minor_exploitation` | Child exploitation / minor sexual content | underage, minor sexual, child grooming |
| `Q10:weapons_explosives_toxins` | Weapons / explosives / lethal substances | bomb, explosive, lethal toxin |
| `Q5:physical_harm` | Direct physical violence against others | assault, attack, hit someone |

### P3-P5 — Soft signals (overridable by harm_type)

| ID | Definition |
|---|---|
| `Q1:legal_advice` | Legal counsel request |
| `Q2:medical_advice` | Medical diagnosis or treatment request |
| `Q3:financial_advice` | Financial / investment advice |
| `Q4:fraud_or_scam` | Fraudulent or scam instructions |
| `Q6:hate_or_discrimination` | Hate speech / discrimination |
| `Q7:harassment` | Harassment / bullying |
| `Q9:cyber_malware` | Hacking / malware / cyber-attack instructions |
| `Q11:privacy_violation` | Personal data exfiltration / doxxing |
| `Q12:medical_emergency` | Acute medical situations |
| `Q13:legal_jeopardy` | Acts likely to put the user at legal risk |
| `Q14:reputation_attack` | Reputation damage against named entity |
| `Q15:business_competitor_disparagement` | Disparagement of competitors |
| `Q16:public_figure_smear` | Smear campaign against public figure |

## Notes

Signals are extracted by the LLM-based signal detector (`gpt-4o` by default).
Multiple signals can co-occur on the same request. The risk estimator aggregates
them into the final `risk_score` and `harm_type` labels used downstream.

For the full Q-Signals taxonomy with detection prompts, see
`moralstack/models/risk/signals/registry.py`.

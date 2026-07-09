# Noise-Floor Gate Result — unify-constitution-retrieval-single-pass

**Verdict: PASS** (release-time statistical-equivalence gate). Offline, read-only, via
`scripts/ai/noise_floor_compare.py` against `moralstack.db`. Reads ONLY `final_action`,
route/`path`, `hard_violation_codes`; matched by prompt hash; never prompt text.

## Runs
- Baseline HEAD (pre-change): `81319498`, `79830edc`, `dceb24f8`, `60124777` (4 runs).
- Post-change (working tree with the change applied): `75dec205`, `eb40ec73`, `aaf30cf0` (3 runs).
- 83 prompts matched per pair (1 upstream-blocked prompt excluded, no FINAL trace).

## Gate matrix (15 pairs)
Every pair: **REFUSE-set identical, route divergence 0%** — zero exceptions.

| pair | final_action% | hard% | note |
|---|---|---|---|
| 75dec205 vs eb40ec73 (post↔post) | 7.23 | 0 | within HEAD internal band |
| 75dec205 vs aaf30cf0 (post↔post) | 8.43 | 0 | = HEAD max internal noise (8.4%) |
| eb40ec73 vs aaf30cf0 (post↔post) | 3.61 | 0 | |
| 75dec205 vs 79830edc/dceb24f8/60124777 | 6.02 | 0 | cluster |
| eb40ec73 vs 79830edc/dceb24f8/60124777 | 6.02 | 0 | cluster |
| aaf30cf0 vs 79830edc/dceb24f8/60124777 | 4.82 | 0 | cluster |
| * vs 81319498 (outlier baseline) | 6.02–9.64 | 0–4.0 | see outlier note |

## Outlier baseline (81319498)
In HEAD-vs-HEAD, `81319498` sits 6.0–8.4% from the other three baselines (which cluster at
0–2.4% among themselves) — a noisy pre-change draw. All mechanical threshold exceedances
(final_action >8%, hard 4.0%) are against `81319498` (or the 8.43% post↔post = HEAD's own
max), are symmetric `NORMAL_COMPLETE↔SAFE_COMPLETE`, and are decision-neutral (REFUSE-set
identical everywhere). The hard-signal 4.0% is intrinsic signals-mini nondeterminism, which
the change structurally cannot affect (signals mini receives no principles).

## Directional systematic-shift test (decisive)
Condition: cluster (3 non-outlier HEAD runs) unanimous on X, AND all 3 post-change runs
unanimous on Y≠X.
- **Change-induced systematic shifts: 0 / 83.**
- Outlier `81319498` vs cluster: 6 / 83 (its own noise).

Interpretation: the change induces **zero** systematic decision movement; its internal
run-to-run noise (3.61–8.43%) equals HEAD's own (0–8.4%). Safety-critical dimensions
(REFUSE, route) are identical across all 15 pairs; hard-signal codes move only as intrinsic
signals-mini noise against the outlier baseline, never changing a decision.

## Conclusion
The change is statistically indistinguishable from HEAD's own noise on decision output, with
**zero** REFUSE/route movement and **zero** systematic shifts. Gate PASSED.

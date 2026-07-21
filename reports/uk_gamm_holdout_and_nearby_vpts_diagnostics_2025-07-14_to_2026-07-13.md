# UK GAMM Hold-out and Nearby VPTS Diagnostics

**Evaluation period:** 14 July 2025 to 13 July 2026 (365 UTC days)  
**Prepared:** 21 July 2026  
**Status:** Research diagnostic. These tests identify model and product-scale
limitations; they do not validate absolute migration intensity.

## Purpose

This diagnostic separates two explanations for the poor absolute agreement in
the external Aloft evaluation:

1. whether the current ERA5 GAMM generalises to a UK radar excluded from its
   own training archive; and
2. whether existing UK and Aloft VPTS have comparable raw profile structure at
   nearby, simultaneous sites before either product is modelled.

No VP, VPTS, or PVOL products were generated or changed. UK VPTS were read
from the JASMIN public object store and Aloft VPTS from the existing local
read-only evaluation cache.

## 1. Held-out UK radar GAMM evaluation

The current `fit_gamm.R` workflow was run on the one-year UK training table
(142,614 hourly observations, 17 radars). For each response and pulse, it
refits the GAMM after excluding one radar, predicts that withheld radar, and
excludes the radar random effect during prediction. This is a genuine
within-archive spatial-transfer test, so it isolates model generalisation from
UK--Aloft processing differences.

| Pulse | Target | Held-out rows | RMSE | MAE | Bias | R2 | Top-decile recall |
|---|---|---:|---:|---:|---:|---:|---:|
| LP | MTR (birds km-1 h-1) | 114,796 | 23.18 | 8.51 | -5.69 | 0.015 | 0.003 |
| LP | VID (birds km-2) | 114,796 | 0.676 | 0.310 | -0.033 | -0.207 | 0.101 |
| LP | u (m s-1) | 72,550 | 11.53 | 8.77 | 0.42 | 0.048 | 0.002 |
| LP | v (m s-1) | 72,550 | 11.80 | 9.03 | 0.20 | 0.029 | 0.002 |
| SP | MTR (birds km-1 h-1) | 27,818 | 50.04 | 23.06 | -11.10 | 0.005 | 0.007 |
| SP | VID (birds km-2) | 27,818 | 3.624 | 2.297 | 1.383 | -13.793 | 0.151 |
| SP | u (m s-1) | 27,033 | 5.81 | 4.49 | 1.08 | 0.304 | 0.647 |
| SP | v (m s-1) | 27,033 | 14.88 | 8.61 | -5.43 | -3.536 | 0.335 |

The LP intensity model has effectively no held-out spatial explained variance
and recalls only 0.34% of held-out top-decile MTR events. The SP intensity
metrics are also poor. This establishes that **the GAMM itself is a material
part of the problem**, independently of any UK--Aloft comparison. It is not
currently an adequate quantitative interpolator for unseen UK radars.

## 2. Nearby simultaneous raw VPTS profile structure

Three closest available cross-channel pairs were selected before calculation:
Falaise--Jersey (153.7 km), Abbeville--Thurnham (155.3 km), and
Jabbeke--Thurnham (171.6 km). Each Aloft 15-minute profile was matched to the
nearest UK LP profile within five minutes, over 200--4,000 m. Common 200 m
altitude levels were compared directly using the source `dens`, `eta`, `dbz`,
`u`, `v`, and `ff` fields.

This is **not** a co-located instrument comparison: spatial separation means
that biological differences are expected. Its purpose is narrower: determine
whether a consistent product-scale relationship exists before attributing
model-vs-Aloft differences entirely to the GAMM.

| Pair | Matched profiles | Matched altitude rows | Median time offset | UK/Aloft median density ratio | Density correlation | u correlation | v correlation |
|---|---:|---:|---:|---:|---:|---:|---:|
| Falaise--Jersey | 30,456 | 609,120 | 108 s | 11.56 | 0.033 | 0.422 | 0.362 |
| Abbeville--Thurnham | 25,793 | 515,860 | 108 s | 14.72 | -0.005 | 0.445 | 0.321 |
| Jabbeke--Thurnham | 25,044 | 500,880 | 108 s | 0.39 | 0.430 | 0.192 | 0.160 |

The first two pairings have median raw UK density values around 12--15 times
their nearby Aloft counterparts. The Jabbeke pairing reverses that relation:
the nearby Aloft density is about 2.6 times the UK value. This is consistent
with the earlier external result where Jabbeke dominated pooled MTR error.
There is no stable raw-density conversion factor across these three sites, and
the low profile-level density correlations at the two French pairs also show
that proximity does not make individual profiles interchangeable.

Velocity components are somewhat more comparable for the French pairings than
density, but their correlations remain moderate at best. Jabbeke--Thurnham
has weak vector correspondence. Thus neither intensity nor vector profiles can
be cross-calibrated by a single network-wide multiplier.

## Conclusion

Both explanations contribute:

- **Model limitation:** the GAMM does not reliably predict intensity at a UK
  radar withheld from the same archive, so its external error cannot be
  assigned solely to the UK VPTS source.
- **Measurement/product limitation:** nearby UK and Aloft raw profiles do not
  share a consistent density scale; the direction of the difference reverses
  at Jabbeke. This makes absolute cross-network MTR/VID comparison invalid
  without explicit site/network calibration.

The appropriate next model step is a held-out-radar, hierarchical model with
radar/network effects and robust log-intensity/event metrics. A calibration
must be fitted and tested separately for LP and SP. Until then, modelled MTR
and VID should be retained as research reanalysis variables, not absolute
measurements.

## Reproducibility

- Held-out GAMM metric source: `scripts/fit_gamm.R`, emitted in
  `gamm_365d/metrics.json` by the full one-year fit.
- Nearby profile diagnostic:
  `scripts/compare_uk_aloft_nearby_vpts.py`.
- Pair-level compact results:
  `reports/data/nearby_vpts_{frcae_jersey,frabb_thurnham,bejab_thurnham}.json`.
- The nearby diagnostic uses UK LP because it is the current preferred UK
  pulse. Aloft VPTS provide no equivalent LP/SP label.

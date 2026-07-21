# Selected UK GAMM: Held-out Radar Evaluation

**Training and evaluation window:** 14 July 2025 to 13 July 2026  
**Selection:** `uk-gamm-heldout-v1`  
**Purpose:** Historical UK radar reanalysis. No forecast claim and no claim of
external absolute calibration.

## Method

The selected model remains a GAMM with the existing projected spatial and
ERA5 predictor structure. It was tuned only through leave-one-UK-radar-out
evaluation of the existing UK VPTS training table. The final model uses
learned cyclic day-of-year and UTC-hour smooths plus their cyclic interaction.
This uses every observed hour and date; it does not apply a night, twilight,
season, or migration-window filter.

Response treatment is selected independently where the held-out evidence
supports that choice:

| Target | Response treatment | Radar random effect during fit |
|---|---|---|
| MTR | Square-root GAMM; MTR weight power 0.20 | Yes, excluded on prediction |
| VID | Cube-root GAMM; profile-count weight | No |
| Bird u/v | Gaussian GAMM; uniform weight | Yes, excluded on prediction |

## Held-out UK Results

| Pulse | Target | Baseline R2 | Selected R2 | Baseline RMSE | Selected RMSE | Selected top-decile recall |
|---|---|---:|---:|---:|---:|---:|
| LP | MTR | 0.015 | 0.234 | 23.18 | 20.44 | 0.353 |
| LP | VID | -0.207 | 0.192 | 0.676 | 0.553 | 0.231 |
| LP | u | 0.048 | 0.060 | 11.53 | 11.45 | 0.000 |
| LP | v | 0.029 | 0.043 | 11.80 | 11.71 | 0.000 |
| SP | MTR | 0.005 | 0.199 | 50.04 | 44.89 | 0.253 |
| SP | VID | -13.793 | -0.165 | 3.624 | 1.017 | 0.163 |
| SP | u | 0.304 | 0.695 | 5.81 | 3.85 | 0.626 |
| SP | v | -3.536 | 0.696 | 14.88 | 3.85 | 0.478 |

The selected model materially improves UK-held-out intensity reconstruction:
LP MTR explained variance increases more than fifteen-fold, with near-zero
pooled bias, and LP VID moves from negative to positive explanatory power.
SP vector transfer is also strong.

## Qualification

The release is suitable as a **UK intensity research reanalysis**: MTR and LP
VID have demonstrated held-out UK-radar skill, and SP vectors have strong
held-out skill. It remains unsuitable for claims of absolute cross-network
intensity calibration because the nearby UK--Aloft profile diagnostic showed
site-dependent product scales.

LP vectors are not reliable away from reporting radars. Product metadata and
the interface must label or suppress LP directional-flow interpretation rather
than presenting these arrows as validated model output. SP VID also remains
below the internal transfer threshold and should retain uncertainty labelling.

## Reproducibility

- Selection configuration: `configs/gamm_uk_holdout_selected.json`
- GAMM runner: `scripts/fit_gamm.R`
- Original diagnostic: `uk_gamm_holdout_and_nearby_vpts_diagnostics_2025-07-14_to_2026-07-13.md`
- Full selected JASMIN run: `artifacts/gamm-experiments/selected_uk_holdout_v1`

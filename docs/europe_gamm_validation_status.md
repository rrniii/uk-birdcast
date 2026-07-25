# Europe GAMM Validation Status

## Release decision

**Do not activate Europe prediction assets.** The Europe tab is deployed in the
UK Bird Maps interface, but it must continue to show `Validation pending` until
an absolute migration-rate model passes independent, held-out radar testing.

This is not an input-provenance failure. It is an external transfer failure for
absolute Aloft-derived MTR.

## Input-fidelity evidence

All source-to-grid checks passed on JASMIN without retaining raw Aloft objects:

| Stage | Evidence | Result |
| --- | --- | --- |
| Aloft streaming source | `artifacts/fidelity/source-summary-8e1f88b.json` | 1,600 source chunks, 42,011 source-days, 971,734 hourly rows passed |
| ERA5 site extraction | `artifacts/fidelity/era5-summary-fed56aa-r2.json` | 365/365 daily reconstructions, 2,803,200 feature rows passed |
| Training assembly | `artifacts/fidelity/training-44b1520.json` | 887,323 training rows, 110 training radars; 109,523 rows at 24 untouched transfer radars passed |
| ERA5 prediction grid | `artifacts/fidelity/grid-summary-c3aab95.json` | 365/365 exact daily rebuilds, 463,944 rows per day passed |

The audited grid uses the same nine predictor contract as the GAMM and includes
the additional vertical winds retained for later experiments. These checks
prove the model sees the intended VPTS-derived response and ERA5 predictors;
they do not demonstrate that absolute MTR transfers between independently
processed radar sites.

## Held-out Aloft transfer results

Every experiment used the same 24-radar untouched Aloft cohort and scores
site-equal `log1p(MTR)` R-squared and each series' own top-decile event F1.
The event definition deliberately uses each series' 90th percentile so a
radar-specific scale offset cannot make every predicted event a false negative.

| Experiment | Median log1p R-squared | Median event F1 | Outcome |
| --- | ---: | ---: | --- |
| Core nine-predictor GAMM, resolved UTC time (`k=800`) | -0.2700 | 0.2346 | Failed |
| Same model with fixed first-quarter radar calibration | 0.0346 | 0.2408 | Failed |
| Aloft-only model with 925/700 hPa winds | -0.4995 | 0.2446 | Failed |
| Extended model with fixed first-quarter calibration | 0.0092 | 0.2593 | Failed |
| Core model with low-rank spatial-time interaction | -2.6528 | 0.0939 | Failed |

The low-rank tensor experiment is recorded at
`artifacts/europe/runs/44b1520-utc-cadence/core-k800-spacetime20-mtr.json`.
The higher-rank exploratory tensor was cancelled after 1 hour 12 minutes: its
non-discretised `mgcv` formulation is too expensive to be a credible release
candidate, and the lower-rank version had already degraded held-out transfer
materially.

## Interpretation

The limiting issue is site and processing dependence in the observed Aloft MTR
scale. It is not defensible to convert this into a universal multiplicative
factor: earlier simultaneous-site checks span both substantial UK/Aloft excess
and deficit. A model trained against those targets cannot be claimed to produce
externally validated absolute birds km-1 h-1 across Europe.

The public tab must therefore remain a visible but data-withheld research
surface. It correctly reports the passing source, ERA5, training and grid
audits while withholding the map data pending external validation.

## Required path to activation

One of the following must be completed before `release_status=published`:

1. A network-harmonised VPTS/MTR reference, with calibrated sampling-volume and
   bird/insect treatment metadata, supports a common absolute scale; or
2. The product is explicitly redefined as a relative migration-activity index,
   with separate target construction, held-out ranking gates and visual units.

Neither option may silently reuse the current absolute MTR labelling or lower
the existing release gates.

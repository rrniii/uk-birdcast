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

## VP-to-VPTS reconstruction audit

The five strongest MTR-scale outliers (`dksam`, `robuc`, `rocra`, `romed`, and
`rotim`) expose BALTRAD VP HDF5 objects rather than PVOL objects. The public
daily VPTS CSV records the immutable `source_file` VP filename for every
profile. The audit samples three dates spanning the model year and compares
each sampled VP directly with the rows that name that source file.

| Check | Result |
| --- | --- |
| VP schema | 25 levels at 200 m spacing (0-4,800 m) at every sampled radar |
| Profile variables | Matching height, density, reflectivity, velocity, quality and count fields |
| Reconstruction samples | 30 VP profiles and 9,765 finite VP/VPTS field-height pairs |
| Value and missingness agreement | Zero numerical difference; zero mismatched missing values |
| Raw input retention | None; the VP HDF5 and daily VPTS files are streamed and discarded |

Derived evidence is stored at
`artifacts/vp-structure-audit/outlier-vp-vpts-reconstruction-20260725.json`.
The runnable audit is `scripts/audit_aloft_vp_structure.py --compare-vpts`.

This rules out daily VPTS assembly as the explanation for the outlier scale in
the sampled objects. The scale difference already exists in the published VP
`dens` and `eta` products. It remains unresolved whether that reflects genuine
local migration, radar/network processing, or both; this audit does not treat
the outlier radars as invalid.

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

## Romania-excluded sensitivity analysis

Romanian radars are not part of the GAMM training table: all seven were in the
untouched transfer cohort. A declared sensitivity experiment therefore keeps
the same 887,323 training rows and excludes only 28,248 Romanian validation
rows (`robar`, `robob`, `robuc`, `rocra`, `romed`, `roora`, `rotim`). The
remaining external cohort has 81,275 rows at 17 radars.

| MTR `k=800` site-equal metric | Full cohort, 24 radars | Excluding Romania, 17 radars |
| --- | ---: | ---: |
| Median raw `log1p` R-squared | -0.2700 | -0.1086 |
| Positive-skill radars | 25.0% | 35.3% (6/17) |
| Median top-decile event F1 | 0.2346 | 0.2537 |
| Median first-quarter calibrated `log1p` R-squared | 0.0346 | 0.0115 |
| Median first-quarter calibrated event F1 | 0.2408 | 0.2598 |

The experiment confirms that the Romanian cluster materially worsens the
pooled result, but the non-Romanian cohort still fails all predeclared
absolute-MTR release gates: raw median skill is negative, fewer than 75% of
sites have positive skill, and event F1 is below 0.50. The calibration number
is descriptive only; it uses each held-out site’s first chronological quarter
and cannot establish a transferable Europe-wide absolute scale.

The derived, hash-locked cohort audit and result are at
`artifacts/europe/runs/44b1520-utc-cadence/non-ro-k800/`. Its contract marks
`publication_eligible: false`; Romanian geography must remain unsupported by
any future restricted-domain product unless separately validated.

## UK, Atlantic and North Sea regional sensitivity analysis

A second declared experiment retained only the requested countries: UK, Ireland,
France, Belgium, Netherlands, Denmark, Germany, Norway, Spain, Portugal and
Iceland. It refit the GAMM on 550,561 rows at 86 radars and evaluated it on
56,366 rows at 11 independent radars in Denmark, Spain, Ireland, Iceland and
Portugal. It excluded 336,762 training rows and 53,157 validation rows outside
the declared regional cohort.

| MTR `k=800` site-equal metric | Non-Romanian Europe, 17 radars | Regional cohort, 11 radars |
| --- | ---: | ---: |
| Median raw `log1p` R-squared | -0.1086 | -0.4634 |
| Positive-skill radars | 35.3% (6/17) | 27.3% (3/11) |
| Median top-decile event F1 | 0.2537 | 0.2679 |
| Median first-quarter calibrated `log1p` R-squared | 0.0115 | 0.0202 |
| Median first-quarter calibrated event F1 | 0.2598 | 0.2805 |

The regional restriction does not improve transferable absolute MTR. Its
event-ranking score rises slightly, but absolute skill degrades and the Danish
transfer radar `dksam` has `log1p` R-squared -9.6762. This regional model is
therefore not a candidate for either a quantitative regional map or a Europe
release. The hash-locked evidence is at
`artifacts/europe/runs/44b1520-utc-cadence/uk-atlantic-northsea-k800/`.

## UK Coastal Corridor Experiment

The coastal-corridor cohort is geographic rather than country-based. It uses
the bundled Natural Earth 1:10m UK coastline and includes continental radars
in Belgium, France, Netherlands, Germany and Denmark only where their shortest
distance to that coastline is at most 350 km. UK radars are then retained only
where they overlap the selected continental sites within the same 350 km
limit. The frozen cohort contains 10 continental radars, 8 UK coastal radars,
and 93,393 derived hourly rows. Southern France, inland Germany, Scandinavia
and Romania are excluded by the geographic rule.

The model is evaluated without site calibration using the required three tests:

| Test | Held-out radars | Median raw `log1p` R-squared | Positive sites | Median event F1 |
| --- | ---: | ---: | ---: | ---: |
| Leave one continental coastal radar out | 10 | -3.9436 | 10.0% | 0.2948 |
| Train continental coast, test UK coast | 8 | -5.5828 | 0.0% | 0.1824 |
| Train UK coast, test continental coast | 10 | -6.4590 | 0.0% | 0.1821 |

The cross-coast tests deliberately use the training network as the prediction
reference and do not apply a source or site calibration. They therefore test
the direct transfer required for an absolute MTR product. Both directions fail
strongly, so this cannot be released as a quantitative UK coastal-corridor
map. A future corridor product must be explicitly relative activity/flow, or
wait for a cross-network calibration that passes held-out coastal radars.

Evidence is stored at
`artifacts/europe/runs/44b1520-utc-cadence/uk-coastal-corridor-k800/`, including
the boundary hash, selected radar locations, derived table hash and site-level
metrics.

## Relative coastal activity and flow product

The failed absolute-MTR corridor model is not used by the public coastal
product. Instead, the separate **Coastal activity & flow** tab reads the same
frozen, derived hourly cohort table and publishes a radar-local activity index:
the empirical percentile of `log1p(MTR)` within that radar over the model year.
Each radar therefore contributes a 0-100 relative activity value, while arrows
show the corresponding VPTS-derived horizontal bird-flow direction only.

This handles the demonstrated source/site scale dependence transparently: the
map must not be read as birds km-1 h-1, a common activity scale, a coast-wide
interpolation, or a forecast. It has no GAMM predictions and does not modify
the read-only UK VPTS, VP or PVOL archive. Its data manifest records hashes of
the frozen derived table and cohort manifest, while daily web partitions retain
only timestamp, radar ID, relative index and direction components.

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

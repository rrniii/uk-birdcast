# UK Bird Maps: External Evaluation with Aloft VPTS

**Evaluation period:** 14 July 2025 to 13 July 2026 (365 UTC days)  
**Prepared:** 21 July 2026  
**Status:** Research evaluation. These results do not yet support a claim that the published modelled migration layer is externally validated.

## Background

UK Bird Maps is being developed as a historical ERA5-driven reconstruction of
radar-derived bird movement. Its current statistical model is a Generalized
Additive Mixed Model (GAMM) trained on UK weather-radar vertical profile time
series (VPTS). The model predicts vertically integrated density (VID),
migration traffic rate (MTR), and horizontal bird velocity from spatial
location and meteorological covariates. It contains no date, season,
day/night, sunrise, sunset, or other phenology term.

The approach is motivated by BirdCast's continental weather-radar framework.
Van Doren and Horton (2018) showed that atmospheric predictors and radar
observations can explain substantial variation in migration intensity across
the United States. That result does not establish that a UK-trained model will
transfer across another radar network: European radar hardware, scan strategy,
signal processing, and biological filtering are heterogeneous. External
evaluation is therefore required before interpreting the UK model field as a
quantitative reconstruction away from UK observations.

This report evaluates the UK GAMM against existing Aloft BALTRAD VPTS. Aloft
provides bird-target vertical profiles processed from European weather radars
with vol2bird. Its BALTRAD collection is operationally broad but not a
homogeneous, independently quality-controlled reference standard. The test is
therefore an external spatial-transfer assessment, not a calibration of the
absolute biological truth.

## Questions

1. Does a GAMM trained on the UK VPTS archive reproduce intensity and velocity
   at independent European radar sites when evaluated at matched ERA5 cells?
2. Are any differences consistent across sites, or do they expose
   site/network-specific calibration and processing effects?
3. Is there enough evidence to describe the present modelled UK field as
   externally validated?

## Data

| Component | Source | Scope used |
|---|---|---|
| UK model training | JASMIN UK bioRad VPTS archive joined to ERA5 | 142,614 hourly rows; 17 UK radars; 14 Jul 2025 to 13 Jul 2026 |
| Meteorology | Existing JASMIN ERA5 0.25 degree grid | Same 365 days; model cells nearest each external radar |
| External observations | Aloft BALTRAD daily VPTS | Seven radars and all overlapping days advertised in the coverage manifest |
| External radar metadata | Aloft OPERA radar metadata | Radar coordinates and site identity |

The candidate external sites were selected before scoring: they had an Aloft
VPTS archive during the model window and an existing model-grid cell within
25 km. No site was selected on the basis of its error. The selected sites and
nearest grid-cell separation were Plabennec (6.7 km), Jabbeke (7.8 km),
Falaise (10.9 km), Treillieres (12.0 km), Shannon (13.1 km), Abbeville
(14.0 km), and Avesnes (14.2 km).

The Aloft HDF5 coverage manifest identified 2,329 expected radar-days.
Daily VPTS CSVs were retrievable for 2,316; 13 were unavailable. Those missing
files are recorded as unavailable, rather than zero movement. After standard
VPTS quality/integration rules, the evaluation contained 18,528 matched valid
hourly profiles for each UK pulse model.

## Methods

### UK GAMM

The GAMM was refit from the current one-year JASMIN training table. Each pulse
was fitted separately. Intensity responses were cube-root transformed for
fitting and back-transformed after prediction. Predictors were projected
easting/northing plus ERA5 850-hPa temperature, 850-hPa relative humidity,
850-hPa u and v wind, surface and mean-sea-level pressure, total cloud cover,
boundary-layer height, and hourly precipitation. A radar random effect was
included during fitting but excluded when predicting at external sites. No time
or phenological predictor was included.

The model was predicted at the pre-existing ERA5 0.25 degree cell nearest each
external radar for every hour in the 365-day window. This uses existing model
inputs and does not generate, alter, or write VP, VPTS, or PVOL products.

### Aloft VPTS aggregation

Existing daily CSV VPTS files were read without modification. Profiles were
integrated from 200 to 4,000 m using the same UK processing convention:

`VID = sum(density * layer width)`

`MTR = sum(density * ground speed * 3.6 * layer width)`

Profiles without finite MTR/VID after gap and rain handling were excluded.
Remaining profiles were averaged to UTC hours. The Aloft files do not carry a
UK LP/SP label, so both UK LP and SP GAMM fits are reported instead of claiming
an exact pulse-equivalent comparison.

### Metrics

For MTR, VID, eastward velocity (u), and northward velocity (v), the report
calculates observed and modelled means, bias (model minus observation), mean
absolute error (MAE), and root mean squared error (RMSE). Pooled metrics are
weighted by matched hourly observations. Because MTR is strongly right-skewed,
pooled means and RMSE should be interpreted with the site-level table, not in
isolation.

## Results

### Pooled external evaluation

| UK model pulse | Metric | N hours | Observed mean | Modelled mean | Bias | MAE | RMSE |
|---|---:|---:|---:|---:|---:|---:|---:|
| LP | MTR (birds km-1 h-1) | 18,528 | 85.12 | 4.95 | -80.17 | 84.11 | 288.51 |
| LP | VID (birds km-2) | 18,528 | 2.91 | 0.35 | -2.56 | 2.72 | 6.27 |
| LP | u (m s-1) | 18,528 | 1.18 | 2.22 | 1.04 | 3.43 | 4.42 |
| LP | v (m s-1) | 18,528 | 0.91 | 1.10 | 0.19 | 2.87 | 3.79 |
| SP | MTR (birds km-1 h-1) | 18,528 | 85.12 | 21.13 | -63.99 | 85.60 | 283.79 |
| SP | VID (birds km-2) | 18,528 | 2.91 | 1.45 | -1.46 | 1.90 | 5.24 |
| SP | u (m s-1) | 18,528 | 1.18 | 5.05 | 3.87 | 6.55 | 8.33 |
| SP | v (m s-1) | 18,528 | 0.91 | 2.05 | 1.14 | 5.36 | 6.69 |

The pooled intensity results are not acceptable as a validation of absolute
MTR or VID. The SP model has a smaller pooled MTR and VID bias than LP, but it
has larger velocity errors and a comparable MTR MAE. Neither is a justified
operational choice on these metrics alone.

### Site-level MTR behaviour

| Site | Files | Matched hours | LP observed mean | LP modelled mean | LP bias | LP RMSE |
|---|---:|---:|---:|---:|---:|---:|
| Abbeville | 356 | 1,509 | 5.3 | 5.2 | -0.1 | 22.7 |
| Avesnes | 338 | 1,187 | 7.2 | 10.5 | 3.3 | 44.7 |
| Falaise | 353 | 2,122 | 5.5 | 5.4 | -0.1 | 29.6 |
| Jabbeke | 350 | 6,876 | 219.0 | 7.4 | -211.6 | 472.1 |
| Plabennec | 349 | 2,237 | 3.4 | 1.2 | -2.3 | 24.3 |
| Shannon | 214 | 2,032 | 14.3 | 0.1 | -14.2 | 38.0 |
| Treillieres | 356 | 2,565 | 2.5 | 2.5 | 0.0 | 13.5 |

The pooled LP result is dominated by Jabbeke, where Aloft MTR is substantially
higher than the UK model output. This is a scientifically useful result: it
demonstrates that uncalibrated cross-network MTR values cannot be treated as a
single absolute scale. At five French sites, mean LP MTR bias ranges from
-2.3 to +3.3 birds km-1 h-1, but event-level RMSE remains 13.5 to 44.7. Thus
good agreement in annual mean is not evidence that individual migration events
are reconstructed accurately.

Shannon is an additional spatial-transfer failure: the LP mean MTR is 0.1
against an observed mean of 14.3 birds km-1 h-1. It is the westernmost
external site and is influenced by a different oceanic setting and a different
radar processing chain. It should not be folded into a UK calibration without
an explicit hierarchical site/network effect.

## Discussion

### Relation to BirdCast and radar-ornithology literature

Van Doren and Horton (2018) showed the potential of combining long radar
archives and meteorology for continental prediction. Their high explained
variation was established within a large, consistently processed NEXRAD-based
system and does not remove the need for independent geographic validation when
the radar network or biological-processing chain changes. The present result
should therefore not be read as a contradiction of BirdCast; it identifies the
additional calibration work required when transferring a UK model to BALTRAD
VPTS.

The Aloft documentation and Desmet et al. (2025) explicitly describe a
heterogeneous European radar network. Aloft's BALTRAD VPTS are produced from
OPERA data with vol2bird, but availability and quality vary by national data
stream, filtering, and upstream radar processing. The Jabbeke contrast is
consistent with this being a measurement-scale and processing problem as well
as a model-transfer problem. It cannot be resolved by simply adding a calendar
or phenology term, which this project deliberately avoids.

The vertical integration itself follows established radar-ornithology practice:
integrating density through altitude and combining density with speed produces
MTR. Curley and Dokter (2025) provide a recent example that derives
height-integrated traffic metrics from vertical radar profiles, while also
showing the importance of preprocessing and explicit treatment of lower-level
coverage. Buler and Diehl (2009) similarly showed that radar-derived bird
density is sensitive to vertical structure and radar sampling geometry.

### What the evaluation supports

- The pipeline can retrieve, parse, quality-handle, and compare existing
  external VPTS at scale without making duplicate radar products.
- The current UK GAMM has plausible mean LP MTR at several nearby French sites,
  including Abbeville, Falaise, and Treillieres.
- The current model does **not** have demonstrated, transferable absolute MTR
  or VID calibration across all external sites.
- LP and SP cannot be selected from this test alone because the external VPTS
  lack a directly equivalent UK pulse label and their performance trade-offs
  differ by variable.

### Main limitations

1. **External VPTS are not ground truth.** They are independent from the UK
   archive, but remain radar-derived products with country-specific processing
   and quality limitations.
2. **No co-located Aloft/UK radar pair exists.** This evaluation tests spatial
   transfer at nearby European sites, not instrument agreement at one physical
   radar.
3. **ERA5-cell mismatch remains.** Sites are 6.7 to 14.2 km from the nearest
   0.25 degree ERA5 cell; this is small relative to the ERA5 grid but matters
   in coastal and convective conditions.
4. **MTR is heavy-tailed.** A small number of large Jabbeke events dominate
   pooled error. Future reports should include log-scale and event-threshold
   diagnostics, not only raw-scale RMSE.
5. **No uncertainty calibration has yet been evaluated.** Model standard errors
   are not a calibrated predictive interval for cross-network VPTS.

## Conclusions and recommendations

The current ERA5 GAMM should remain labelled **research reanalysis**. It is
not externally validated for absolute migration intensity. The full available
Aloft comparison found substantial site dependence, severe pooled intensity
bias, and event-level errors even where annual mean MTR was close.

Before publishing a modelled intensity layer as quantitative:

1. Fit a hierarchical calibration model with explicit radar/network random
   effects and a held-out-site validation design.
2. Retain separate LP and SP candidates until a UK pulse-to-Aloft processing
   equivalence study is completed.
3. Add log-MTR, high-event recall, direction, and uncertainty-coverage metrics.
4. Validate against independent non-radar information where feasible (for
   example, migration-count, acoustic, thermal, or ring-recovery products),
   rather than using radar-to-radar agreement alone.
5. Publish the external-evaluation table beside any model release and prevent
   the interface from implying that the model is an observed measurement.

## Reproducibility

- Model fit: `scripts/fit_gamm.R`
- External evaluator: `src/birdcast_uk/external_validation.py`
- Full-period runner: `scripts/run_aloft_external_validation.py`
- Consolidated results: `aloft_external_evaluation_full.json` and
  `aloft_external_evaluation_table.csv` in the evaluation run directory.
- All source VPTS objects were read only. The evaluation creates only compact
  prediction and report artefacts.

## References

- Van Doren, B. M. & Horton, K. G. (2018). *A continental system for
  forecasting bird migration*. Science, 361, 1115-1118.
  https://doi.org/10.1126/science.aat7526
- Desmet, P. et al. (2025). *Biological data derived from European weather
  radars*. Scientific Data. https://doi.org/10.1038/s41597-025-04641-5
- Nilsson, C. et al. (2019). *Revealing patterns of nocturnal migration using
  the European weather radar network*. Ecography, 42, 876-886.
  https://doi.org/10.1111/ecog.04003
- Curley, S. & Dokter, A. (2025). *Integrated vertical profiles for 16 coastal
  NEXRAD weather surveillance radars from 2014 to 2023*. Dryad.
  https://doi.org/10.5061/dryad.zcrjdfnrr
- Buler, J. J. & Diehl, R. H. (2009). *Quantifying bird density during
  migratory stopover using weather surveillance radar*. IEEE Transactions on
  Geoscience and Remote Sensing, 47, 2741-2751.
  https://doi.org/10.1109/TGRS.2009.2014463

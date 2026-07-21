# UK GAMM Iteration Log

**Evaluation window:** 14 July 2025 to 13 July 2026  
**Model family:** BirdCast-style ERA5 GAMM with projected spatial smooths,
cyclic annual/diurnal terms, and leave-one-UK-radar-out evaluation.

This log records parameter changes considered after the initial UK/Aloft
diagnostic. All results below are pooled leave-one-radar-out UK VPTS metrics.
They are selection evidence, not an external absolute-density calibration.

## Retained Benchmark

`configs/gamm_uk_holdout_selected.json` remains the benchmark. It uses the
original 850-hPa ERA5 predictor set, spatial rank 10, cyclic day/hour terms
and their interaction, a square-root MTR response with MTR^0.2 weights, and
a radar random effect excluded during held-out prediction.

| Pulse | R2 | RMSE | MAE | Bias | Top-decile recall |
|---|---:|---:|---:|---:|---:|
| LP MTR | 0.2343 | 20.4383 | 9.3340 | -0.3214 | 0.3529 |
| SP MTR | 0.1989 | 44.8888 | 22.0858 | -4.3244 | 0.2531 |

## Rejected 850-hPa Variants

| Change | LP R2 | LP RMSE | SP R2 | SP RMSE | Decision |
|---|---:|---:|---:|---:|---|
| Remove radar random effect | 0.1872 | 21.0568 | 0.1780 | 45.4693 | Reject: weaker both pulses |
| Spatial smooth rank 6 | 0.2343 | 20.4382 | 0.1993 | 44.8767 | Reject: effectively unchanged LP and lower event recall |
| Spatial smooth rank 14 | 0.2299 | 20.4973 | 0.2013 | 44.8217 | Reject: LP regression |
| ERA5 univariate smooth rank 6 | 0.2284 | 20.5166 | 0.2009 | 44.8316 | Reject: LP regression |
| ERA5 univariate smooth rank 14 | 0.2333 | 20.4513 | 0.1997 | 44.8663 | Reject: LP regression |
| Smoother cyclic time ranks (14/8) | 0.2319 | 20.4705 | 0.1921 | 45.0772 | Reject: weaker both pulses |
| Detailed cyclic time ranks (28/16) | 0.2299 | 20.4969 | 0.2022 | 44.7958 | Reject: LP regression |
| 850-hPa wind tensor interaction | 0.2307 | 20.4857 | 0.1988 | 44.8893 | Reject: no transfer gain |
| 850-hPa temperature-humidity interaction | 0.2316 | 20.4745 | -5.5801 | 128.6466 | Reject: severe SP instability |

These are controlled GAMM parameter changes only. They retain the same
archive, response target, radar-wise hold-out protocol, and all-hour/year
sampling used by the benchmark.

## Next Evaluation

A separate full-coverage ERA5 archive with 925, 850, and 700 hPa wind fields
is being built in an experiment-only path. Its rebuild guard requires an exact
match to the baseline UK table's row and pulse counts before fitting. The
following BirdCast-style GAMM candidates will be evaluated automatically:

1. all vertical winds as additional smooth predictors;
2. a 925-hPa-only directional predictor treatment; and
3. a low-rank 925-hPa wind interaction.

The candidate comparison gate requires primary intensity non-regression (no
R2 loss greater than 0.01 for LP MTR, LP VID, or SP MTR) and a mean LP vector
R2 gain of at least 0.02 before a vertical candidate is eligible for follow-up.

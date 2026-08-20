# UK BirdCast implementation contract

## Operational scope

- Historical observations and historical model reanalysis only.
- No operational forecast or ECMWF Open Data product.
- VPTS-derived VID integrated over 200-4000 m.
- LP and SP retained separately; no combined LP+SP interpretation.
- All-hour UTC products with measured effort, coverage, and rain flags.
- Immutable daily assets with atomic `latest` manifest promotion.
- Natural Earth geometry is display context, never a scientific mask.

VID in birds km-2 is a radar passage index, not an absolute bird count or
population estimate.

## Storage and compute boundary

The source VPTS archive remains read-only in the JASMIN Object Store. Archive
analysis, ERA5 retrieval, feature extraction, model inference, validation, and
publication run on JASMIN batch/GWS. Raw VPTS, ERA5 files, model objects,
prediction tables, logs, and credentials remain private.

The browser receives only compact historical and modelled manifests plus their
referenced web assets. The JASMIN Cloud host serves those objects and the static
shell; it does not produce data.

## Observation contract

For each non-gap altitude layer:

```text
VID = sum(density_birds_km3 * layer_width_km)
MTR = sum(density_birds_km3 * speed_ms * 3.6 * layer_width_km)
```

Hourly circular direction uses aligned finite direction/MTR pairs from
non-rain profiles. Missing speed or direction remains missing and is never
interpreted as calm. Every aggregate records its pulse, source provenance,
coverage, and applicable quality flags.

## Historical ERA5 contract

ERA5 is an independent retrospective weather flow. Training rows are
complete-case for the declared predictor set: 850 hPa temperature, relative
humidity, `u` and `v` wind; surface pressure; mean sea-level pressure; total
cloud cover; boundary-layer height; and hourly precipitation. Required values
are never silently dropped or imputed as zero.

Monthly Earthkit retrievals are split into atomic daily pressure and
single-level files. Readiness requires both source families, exact configured
radar coverage, all 24 UTC hours, and successful feature validation.

## Selected model contract

Production selection is `uk-gamm-heldout-v2-sp-vector-925`:

| Target | LP | SP |
| --- | --- | --- |
| MTR | Selected 850 hPa GAMM | Selected 850 hPa GAMM |
| VID | Selected 850 hPa GAMM | Selected 850 hPa GAMM |
| Bird `u`/`v` | Selected 850 hPa control GAMM | 925 hPa wind-interaction GAMM |

The fit derives cyclic day-of-year and UTC-hour predictors from `time_utc` and
includes their cyclic interaction. This is a learned temporal model, not a
hard-coded season or solar-period filter. Day, night, and twilight observations
remain eligible when all quality and predictor requirements pass.

The selected component manifest records each model path and SHA-256. Inference
must verify those hashes before reading a model. LP vector transfer and SP VID
retain the limitations recorded in the selection/publication manifests.

Family-level GAMM/XGBoost fitting remains available for controlled research,
but it is not a production selection mechanism.

## Release completeness

A modelled release must declare its exact inclusive start/end dates and day
count. Every day must contain the canonical `00:00`-`23:00Z` frames, the exact
fixed coordinate set, one finite required value per cell/hour, and identical
LP/SP coverage. A filename count alone is not evidence of completeness.

The production sequence is:

1. verify immutable inputs and selected component hashes;
2. predict daily private partitions;
3. reconcile dates, hours, cells, targets, and pulse products;
4. build compact public `historical` and `gam-era5` products;
5. create a product-scoped, hash-bearing publication plan;
6. upload immutable assets, then update `latest` manifests last;
7. verify the public objects and uncached browser.

No run, model, log, raw-data, or credential path belongs beneath the public
artifact root.

## Forecast denial contract

Forecast and ECMWF code is retained for provenance and research. Production
timers remain disabled and the forecast-enable sentinel remains absent.
Static refreshes must emit an explicit unavailable forecast manifest rather
than preserve stale validity times.

Reactivation is blocked until the source latency/completeness contract and
per-radar timestamp/freshness checks are implemented. One fresh radar must not
authorize stale radar observations, and missing data must not be treated as
zero density or calm wind. Full criteria are in [DEPLOYMENT.md](../DEPLOYMENT.md).

## Public validation

Only aggregate BTO validation results may be public. Licensed source records
remain private and are not used to claim absolute calibration. UK-to-Aloft
comparison reports likewise contain aggregate metrics and explicit mapping
provenance, never source profiles.

The public bucket must allow anonymous browser `GET` and `HEAD` for intended
objects. CORS grants no write permission. A public HTTP 200 is necessary but
does not replace manifest, asset, coverage, provenance, or deployment-SHA
verification.

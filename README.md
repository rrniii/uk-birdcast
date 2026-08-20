# Live UK Bird Maps

Live UK Bird Maps publishes retrospective bird-passage observations and a
historical weather-linked reanalysis from the production UK bioRad VPTS
archive. It reads source archives without modifying or republishing them.

## Product status

| Product | Operational state | Interpretation |
| --- | --- | --- |
| UK observed | Historical only | Radar-derived passage indices; not population estimates |
| UK modelled | Historical reanalysis only | Selected UK GAMM components; not a forecast or external absolute calibration |
| UK forecast | Unavailable, fail-closed | Forecast and ECMWF production remain disabled |
| Europe absolute GAMM | Withheld | Failed external transfer gates |
| Europe relative activity | Research product | Radar-local percentile activity and flow direction, not absolute intensity |

The browser is a consumer of immutable public objects. Heavy VPTS processing,
ERA5 retrieval, model execution, validation, and publication run on JASMIN
batch/GWS. The cloud host has no source-data or model-training role.

## Scientific contract

For altitude-layer width `dh` in km and bird density `dens` in birds km-3:

```text
VID = sum(dens * dh)                  birds km-2 per profile
```

The primary altitude interval is 200-4000 m. VID is a passage index, not an
absolute number of birds. LP and SP have different sampling characteristics;
they are retained as separate products and are never added together.

## Selected UK model

Production reanalysis is pinned to selection
`uk-gamm-heldout-v2-sp-vector-925`:

| Component | Selected model |
| --- | --- |
| LP and SP MTR/VID | Selected 850 hPa GAMM |
| LP bird `u`/`v` | Selected 850 hPa control GAMM, with weak-transfer warning |
| SP bird `u`/`v` | 925 hPa wind-interaction GAMM |

The GAMMs learn cyclic day-of-year and UTC-hour smooths plus their cyclic
seasonal-diurnal interaction. They do not apply a hard-coded season, night,
twilight, sunrise, sunset, or migration-window filter. Training is all-hour,
pulse-separated, complete-case across the declared ERA5 predictors, and uses
projected spatial coordinates.

The reviewed fits use a square-root response for MTR, a cube-root response for
VID, and identity-scale vector components. Publication therefore squares MTR,
cubes VID, and leaves `u`/`v` unchanged. Reported uncertainty is the GAMM
linear-predictor standard error, not a calibrated response-scale interval.

The component decision and exact reviewed hashes are defined once in
[`src/birdcast_uk/selected_model.py`](src/birdcast_uk/selected_model.py), with
operator-facing metadata in
[`configs/gamm_uk_holdout_component_publication.json`](configs/gamm_uk_holdout_component_publication.json).
The evidence window was 14 July 2025
through 13 July 2026; publication coverage is separately manifest-driven and
must pass exact date, 24-hour, grid, and LP/SP reconciliation. See
[`reports/uk_gamm_selection_2025-07-14_to_2026-07-13.md`](reports/uk_gamm_selection_2025-07-14_to_2026-07-13.md).

The family-level GAMM/XGBoost comparison remains a research benchmark. It is
not allowed to replace the selected production component manifest.

## Data flow

1. Freeze the VPTS inventory and ERA5 inputs, including source identities and
   checksums.
2. Build pulse-separated hourly observations and complete ERA5 features on
   JASMIN.
3. Run the selected component models for an explicitly declared contiguous
   date range.
4. Reconcile every day, canonical UTC hour, fixed-grid cell, required target,
   and LP/SP product.
5. Build only the `historical` and `gam-era5` public product manifests and
   their referenced assets.
6. Hash the publication plan, upload immutable assets, then promote the
   `latest/*.json` manifests last.
7. Verify the deployed SHA, public manifests, referenced assets, browser route,
   and disabled forecast services.

The production entry point is
[`deploy/slurm/submit-selected-reanalysis.sh`](deploy/slurm/submit-selected-reanalysis.sh).
The detailed release and rollback procedure is in [DEPLOYMENT.md](DEPLOYMENT.md).

## Why forecasting is unavailable

Radar observations arrive with variable latency. The retained forecast code
now timestamps and rejects stale radars independently, but there is not yet an
accepted end-to-end availability, completeness, and latency contract for the
operational feed. Missing observations must not become zero density or calm
wind, and a technically successful run is not evidence that coverage is fit
for an operational forecast.

Forecast and ECMWF production therefore fail closed: their timers remain
disabled and their services additionally require the absent
`/etc/birdcast-uk/forecast-enabled` sentinel. Re-enabling them requires the
acceptance criteria in [DEPLOYMENT.md](DEPLOYMENT.md), not merely a successful
test invocation or downloaded ECMWF cycle.

## Archive access and Aloft comparisons

UK Bird Maps reads existing UK VPTS CSV objects and streams published Aloft
VPTS. An individual VP is selected in memory. The project does not create a
replacement VP/VPTS/PVOL archive.

```bash
birdcast-uk archive aloft-coverage \
  --radar seang --start-day 2020-08-29 --end-day 2020-08-30 \
  --source baltrad --output /path/to/aloft-coverage.json

birdcast-uk archive crosswalk \
  --uk-radars data/historical-input/radars.json \
  --mappings configs/aloft_crosswalk.example.json \
  --output /path/to/crosswalk.json
```

Only explicitly reviewed physical-radar or documented nearby-radar mappings
are compared. Public comparison outputs contain compact aggregate metrics and
provenance, never profile rows.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[birdcast,dev]"
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/pytest -q
```

`main` is the single integration and release branch. Use short-lived topic
branches and deploy an exact reviewed commit from `main`; see
[CONTRIBUTING.md](CONTRIBUTING.md).

## Routes and Europe workflow

The canonical UK route is `/live-uk-bird-maps/`; `/birdcast-uk/` is a permanent
compatibility redirect. Public data remain under the `birdcast-uk/` Object
Store prefix.

The Europe research workflow is part of `main`, not a parallel branch. It
streams Aloft BALTRAD inputs, reads UK SP data immutably, and maintains
independent validation and publication gates. See
[`docs/EUROPE_GAMM.md`](docs/EUROPE_GAMM.md) and
[`docs/europe_gamm_validation_status.md`](docs/europe_gamm_validation_status.md).

# Daily UK Bird Maps publication

The observation archive and modelled maps are **retrospective products**, not
real-time observations or forecasts. Both are updated automatically as their
inputs become complete. Never enable a forecast/ECMWF production timer as part
of this workflow; the separate forecast latency contract remains unresolved.

## Two independent, source-limited cycles

| Product | Production schedule (UTC) | Complete-day target |
| --- | --- | --- |
| Observations | 00:35, 06:35, 12:35, 18:35 | Common latest UTC source date across all configured radars, minus one day for the local solar-day boundary |
| Modelled migration | 02:20 daily | UTC today minus six days, with all 24 ERA5 hours and required pressure levels verified |

Cron on `cron-01.jasmin.ac.uk` submits bounded LOTUS jobs. It does not do the
analysis itself. Submit and execution locks plus Slurm singleton prevent
overlap. A failed run is retried on the next scheduled invocation, retaining
the last good publication. The first model cycle catches up at most 60 days
(12-hour batch limit); subsequent cycles resume from the actual public manifest.

[ECMWF documents daily ERA5T updates about five days behind real time, with no
fixed release hour](https://confluence.ecmwf.int/pages/viewpage.action?pageId=669811810).
The six-day target provides one complete-day margin, not a promise that the
source can never be delayed. Recent weather may be preliminary ERA5T and may
later be revised by ECMWF. We retain the retrieved inputs and immutable daily
model snapshots; this cycle does not silently replace them with revised weather.

## Scientific contract

The selected `uk-gamm-heldout-v2-sp-vector-925` models are **not retrained**.
The manifest and all eight component RDS files are SHA-256 checked. The original
2025-07-14 to 2026-07-13 selection/evidence archive, colours and interpretation
are preserved. Later dates are labelled frozen-model retrospective extensions,
not a newly validated time window. LP vector transfer limitations and the
linear-predictor-standard-error uncertainty interpretation still apply.

The original full-archive reproduction command retains its exact 365-day gate.
Only `birdcast_uk.model_cycle` opts out of the ERA5 training-date restriction;
it still uses the original training feature ranges for spatial/weather support.
The physical radar-range grid must match the published grid exactly. Both
pulses need the same cells in every UTC hour, finite predictors (including
925 hPa winds), finite predictions and nonnegative standard errors/intensities.

## Publication transaction and recovery

Each new UTC day creates private run inputs and an immutable public version.
The updater plans only LP, SP and provenance JSON plus `latest/gam-era5.json`.
It never uploads private model files, training rows, observations or forecasts.
All earlier asset references remain unchanged. Immutable uploads are independently
GET/hash checked before latest is promoted, and all four objects are checked
again before the success checkpoint is written. Public latest is compared with
the saved base before each upload phase to detect out-of-band writers.

State lives in `birdcast-uk/data/model-cycle`: `raw/`, `site-features/`, `runs/`,
`logs/`, `published.json`, and `cycle-status.json`. A failure never advances
`published.json`. If a process stops after promoting latest, the next invocation
uses public latest as its authority and verifies the published tail before
continuing. Retained interrupted run directories are evidence, not checkpoints.
Do not delete them or change an existing public day to force a retry.

## Install or promote a release

1. Test and commit on `main`; deploy that exact clean SHA to a new `releases/SHA`
   directory. Create a release-local venv including `earthkit-data`, `cdsapi`, `xarray`,
   `netCDF4`, `pyproj` and `s3cmd` (the observation-only runtime is insufficient).
   JASMIN jobs load `jaspy/3.12/v20250704` and `jasr/4.4/v20250704` (mgcv/jsonlite).
2. Install the model environment from
   `deploy/env/birdcast-uk.model-cycle.env.example` as a private mode-0600 file
   outside Git. Use the selected manifest and radar-range `training.json` contract, not
   a research candidate. Keep CDS/object-store credential values out of logs.
3. On `cron-01`, run `deploy/scripts/install-historical-cron.py --product model`
   with `--submitter /releases/SHA/deploy/scripts/submit-model-cycle.sh`,
   `--environment /private/model-cycle.env`, and a private `--backup-dir`.
   The installer verifies a recovery copy and preserves other cron blocks.
   Omitting `--product` maintains the existing observation installation command.
4. Trigger `bash /releases/SHA/deploy/scripts/submit-model-cycle.sh /private/model-cycle.env`.
   Verify the job, public LP/SP tail and latest dates; submission alone is not success.
5. Promote the same web code SHA and refresh the UK static site and freshness
   report. The hourly freshness check independently flags source delay,
   observation backlog, model backlog (>2 days beyond the six-day weather
   allowance), missing products and accidental forecast availability. The site
   displays observation and model data dates separately.

Rollback means selecting a previously verified manifest and code release with
explicit operator approval; no archive deletion or model retraining is needed.
Do not alter upstream Avocet/ICECAPS jobs, original research checkouts, or unrelated
EU/coastal products when maintaining these two UK publication cycles.

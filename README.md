# Live UK Bird Maps

Live UK Bird Maps publishes historical bird-passage observations and
reanalyses from the production UK bioRad VPTS archive. It is a standalone
consumer of immutable objects and does not run or modify the underlying
radar-data production pipeline.

The public product is intentionally historical. Radar delivery is delayed, so
forecast generation and ECMWF Open Data retrieval are dormant. ERA5 is retained
as an independent historical weather flow for attribution and model analysis.

## Archive access and Aloft comparisons

UK Bird Maps reads the existing UK VPTS CSV archive directly from the JASMIN
Object Store. Aloft VPTS are read directly from the published Aloft bucket.
An individual VP is selected in memory from an existing VPTS object; the project
does not write new VP or VPTS files, recalculate a replacement archive, or
republish source data.

Discover daily Aloft files for a known radar and period:

```bash
birdcast-uk archive aloft-coverage \
  --radar seang --start-day 2020-08-29 --end-day 2020-08-30 \
  --source baltrad --output /path/to/aloft-coverage.json
```

Create the explicit UK-to-Aloft comparison crosswalk:

```bash
birdcast-uk archive crosswalk \
  --uk-radars data/historical-input/radars.json \
  --mappings configs/aloft_crosswalk.example.json \
  --output /path/to/crosswalk.json
```

The crosswalk intentionally starts empty. Add a pair only after confirming that
the two identifiers represent the same physical radar, or classify it as a
documented nearby-radar comparison. The comparison command consumes two
existing daily VPTS URLs and writes only a compact metrics/provenance report.
Build the browser-facing index from that explicit crosswalk and a directory of
such reports:

```bash
birdcast-uk archive comparison-index \
  --crosswalk /path/to/crosswalk.json \
  --reports-dir /path/to/comparison-reports \
  --output /path/to/archive/comparisons/latest.json
```

The dashboard reads this optional index only. It never downloads, rewrites, or
publishes profile rows from either archive.

## Data flow

1. Read VPTS products from the public Object Store catalogue under
   `ukmo-nimrod/vpts/current_ci_le4/`.
2. Run the archive-scale VPTS analysis on JASMIN batch compute, keeping LP and SP
   as separate products.
3. Stage the compact aggregate package for the cloud web host.
4. Build yearly radar-day JSON, archive summaries, scientific SVG plots, and a
   Natural Earth 1:10m coastline reference.
5. Publish immutable historical assets before atomically updating
   `birdcast-uk/latest/historical.json`.
6. Join historical radar summaries to the standalone Earthkit/ERA5 flow.
7. Fit an all-hour, pulse-separated ERA5 GAMM and an identical-predictor XGBoost benchmark on JASMIN batch compute.
8. Select one model family using held-out-radar performance, publish hourly
   native-ERA5 flow frames across the union of physical radar ranges on land
   and water, and compare aggregate activity with licensed BTO products.

The first modelled release covers the latest complete 365-day overlap between
the VPTS archive and ERA5. Published manifests record the exact input-file,
profile, and radar-hour counts for each run; the public interface does not
claim a fixed archive total.

## Scientific contract

For altitude layer width `dh` in km and density `dens` in birds km-3:

```text
VID = sum(dens * dh)                  birds km-2 per profile
```

The primary altitude interval is 200-4000 m. The web map and plots use VID as a
passage index. It is not an absolute count of individuals or a population
estimate. LP is the default product; LP and SP are never added together.

## Modelled flow reanalysis

The Modelled migration tab is historical only. It uses the latest complete 365-day
overlap between VPTS and ERA5, at hourly UTC cadence. The training contract
contains no timestamp, hour-of-day, season, daylight, twilight, sunrise or
sunset predictor. It fits separate LP/SP models for MTR, VID and bird ground
velocity components. A GAMM (`mgcv::bam`) is the primary interpretable model;
XGBoost uses the same ERA5 and spatial inputs as a benchmark. XGBoost is
published only when it improves every held-out-radar MTR/VID comparison by at
least 10%, improves top-decile event detection, and does not worsen vectors.

The JASMIN entrypoint is `deploy/slurm/submit-historical-reanalysis.sh`. It
runs GAMM and XGBoost as independent Slurm jobs before model selection so the
one-CPU standard QoS does not serialize the candidate fits. The national ERA5
grid input must carry a support score for every cell within at least one
radar's validated physical range. Land boundaries are never used to mask,
clip, or score the product. Unsupported extrapolation is faded in the web map
rather than hidden or presented equally.
Training rows must be complete for all nine ERA5 predictors. The annual
Earthkit backfill uses at most two concurrent calendar-month requests, splits
their responses into atomic daily files, and is followed by an exact 365-day
radar-hour reconciliation gate; no join or model fit can run from a partial
weather archive.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[birdcast,dev]"
.venv/bin/pytest
```

## Historical build

```bash
birdcast-uk radars from-pvol-catalog --output data/radars.json

birdcast-uk historical build \
  --source-dir /path/to/current_ci_le4_full \
  --output-root data/static-artifacts \
  --radars data/radars.json
```

The source directory must contain `analysis_summary.json`, `daily_totals.csv`,
`network_annual_seasonal_totals.csv`, `phenology.csv`, and `coverage.csv`.
Deployment files for the JASMIN Cloud host are under `deploy/`.

The canonical web route is `/live-uk-bird-maps/`; `/birdcast-uk/` is retained
as a permanent compatibility redirect. The Object Store prefix remains
`birdcast-uk/`.

# UK BirdCast

UK BirdCast publishes historical bird-passage reanalyses from the production UK
bioRad VPTS archive. It is a standalone consumer of immutable objects and does
not run or modify the underlying radar-data production pipeline.

The public product is intentionally historical. Radar delivery is delayed, so
forecast generation and ECMWF Open Data retrieval are dormant. ERA5 is retained
as an independent historical weather flow for attribution and model analysis.

## Data flow

1. Read VPTS products from the public Object Store catalogue under
   `ukmo-nimrod/vpts/current_ci_le4/`.
2. Run the archive-scale VPTS analysis on JASMIN batch compute, keeping LP and SP
   as separate products.
3. Stage the compact aggregate package for the cloud web host.
4. Build yearly radar-day JSON, archive summaries, scientific SVG plots, and a
   Natural Earth 1:10m UK boundary.
5. Publish immutable historical assets before atomically updating
   `birdcast-uk/latest/historical.json`.
6. Join historical radar summaries to the standalone Earthkit/ERA5 flow.
7. Compare aggregate phenology and event timing with licensed BTO products.

The first published snapshot contains 116,721 VPTS files, 23.8 million vertical
profiles, and 344,946 radar-day summaries spanning 2013-2026.

## Scientific contract

For altitude layer width `dh` in km and density `dens` in birds km-3:

```text
VID = sum(dens * dh)                  birds km-2 per profile
```

The primary altitude interval is 200-4000 m. The web map and plots use VID as a
passage index. It is not an absolute count of individuals or a population
estimate. LP is the default product; LP and SP are never added together.

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

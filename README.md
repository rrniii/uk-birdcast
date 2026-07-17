# UK BirdCast

UK BirdCast builds observed and forecast bird-migration products from the
production UK BioRad VPTS dataset, an independent ERA5 flow, and archived ECMWF
Open Data forecast cycles. It is a standalone consumer of immutable objects; it
does not run or modify the underlying UK radar processing pipeline.

## Data flow

1. Read the small public VPTS catalogue.
2. Resolve deterministic CSV keys with bounded HTTP HEAD requests.
3. Select LP for each radar/date, using SP only when LP is unavailable.
4. Download only the rolling date window required for complete nights.
5. Convert each vertical profile to VID and MTR, apply gap/rain QC, and
   time-integrate MTR across sunset-to-sunrise nights.
6. Publish nightly, hourly, map, health, and provenance artifacts.
7. Join hourly observations to the standalone Earthkit/ERA5 feature files.
8. Archive ECMWF Open Data forecast cycles through Earthkit.
9. Assimilate fresh radar densities into a process-guided ensemble state and
   publish a national 10 km reanalysis plus 96-hour forecast.

The source archive is:

```text
ukmo-nimrod/vpts/current_ci_le4/{radar}/{year}/{YYYYMMDD}_{lp|sp}_vpts.csv
```

Discovery is catalogue/cursor driven. No command recursively lists or downloads
the full VPTS archive.

## Scientific contract

For altitude layer width `dh` in km, density `dens` in birds km-3, and ground
speed `ff` in m s-1:

```text
MTR = sum(dens * ff * 3.6 * dh)       birds km-1 h-1
MT  = integral(MTR, time)             birds km-1 per night
VID = sum(dens * dh)                  birds km-2
```

The primary altitude interval is 200-4000 m. Gap-filled layers are excluded.
Profiles are flagged as rain-suspect when at least 80% of finite DBZH layers at
or below 2 km exceed 7 dBZ. Time integration does not bridge gaps longer than
30 minutes.

## Development

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[birdcast,dev]"
.venv/bin/pytest
```

## Key commands

```bash
birdcast-uk vpts inventory \
  --output data/vpts_inventory.json \
  --cursor data/vpts_cursor.json

birdcast-uk observed build \
  --input data/vpts_inventory.json \
  --input-kind inventory \
  --cursor data/vpts_cursor.json \
  --output-dir data/static-artifacts

birdcast-uk features join-era5 \
  --observed-hourly data/static-artifacts/latest/latest_observed_hourly.json \
  --era5-dir data/static-artifacts/era5/features \
  --output data/static-artifacts/latest/latest_model_features.json

birdcast-uk ecmwf archive-cycle --output-root data/ecmwf/cycles

birdcast-uk forecast build \
  --observed-hourly data/static-artifacts/latest/latest_observed_hourly.json \
  --radars data/radars.json \
  --output-root data/static-artifacts
```

The gridded model, stale-data behaviour, training split, output schema, and BTO
validation boundary are specified in `docs/IMPLEMENTATION.md`.

Deployment files for the JASMIN Cloud host are under `deploy/`.

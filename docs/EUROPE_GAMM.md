# Europe-wide GAMM

The `birdcast_euro` workflow fits one source-aware GAMM across the eligible
Aloft BALTRAD archive and the immutable UK SP VPTS archive. It is separate from
the UK model and dashboard.

## Data contract

| Input | Access | Persisted by this workflow |
|---|---|---|
| Aloft coverage and daily VPTS | Streamed from the public Aloft object store | Hourly Parquet and source audit only |
| UK SP VPTS | Read-only from the JASMIN Object Store | Existing hourly UK training rows |
| ERA5 | Europe-specific Bird Maps flow | Site and fixed-grid feature Parquet |
| Raw VP, VPTS and PVOL | Source archives only | Never copied or regenerated |

No timestamp, hour, daylight, twilight, season or phenology term enters the
model. The model uses 200-4000 m profiles, hourly UTC cadence, Aloft BALTRAD as
the reference source, and UK SP as a separately estimated source effect.

## Reproducible run

Lock the full eligible Aloft cohort before examining model errors:

```bash
birdcast-uk europe cohort \
  --minimum-training-days 365 \
  --output artifacts/europe/source/aloft-cohort.json

birdcast-uk europe chunk-manifest \
  --cohort artifacts/europe/source/aloft-cohort.json \
  --output artifacts/europe/source/aloft-radar-months.jsonl
```

Submit the radar-month array. Each task streams one daily CSV at a time and
writes one restartable compressed hourly partition. Set the upper bound to
`chunk_count - 1`:

```bash
sbatch --array=0-13698%200 deploy/slurm/birdcast-euro-aloft-stream.sbatch
```

Generate model metadata from the derived partitions. The two-letter OPERA
radar prefix supplies the initial country stratum; `--overrides` is a reviewed
correction file for identifiers that do not follow that convention.

```bash
python scripts/build_europe_radar_metadata.py \
  --aloft-parquet 'artifacts/europe/hourly/**/*.parquet' \
  --uk-radars data/historical-input/radars.json \
  --overrides configs/europe_radar_overrides.json \
  --output artifacts/europe/source/radars.json
```

Run the Europe-specific ERA5 flow over the area in `birdcast_uk.config`,
extracting hourly features at each radar and on the fixed 0.25 degree grid.
The grid includes land and water and retains cells up to 250 km from a radar.

Assemble the harmonised hourly table:

```bash
python scripts/assemble_europe_training.py \
  --aloft-parquet 'artifacts/europe/hourly/**/*.parquet' \
  --uk-training-csv artifacts/uk/training/hourly_era5.csv \
  --era5-parquet 'artifacts/europe/era5/sites/**/*.parquet' \
  --radar-metadata artifacts/europe/source/radars.json \
  --validation-output artifacts/europe/training/europe_transfer_validation_hourly_era5.csv \
  --output artifacts/europe/training/europe_hourly_era5.csv
```

Radars with at least 365 advertised days enter the fit. Shorter eligible
records are written only to the transfer-validation table and are predicted
after the final fit; they never contribute model weights or coefficients.

Freeze `configs/gamm_europe_aloft_uk_sp.json` with an immutable training path
and run identifier, then submit:

```bash
export BIRDCAST_EURO_ROOT=$PWD
export BIRDCAST_EURO_MODEL_SPEC=$PWD/artifacts/europe/run/model-spec.json
export BIRDCAST_EURO_GRID_TABLE=$PWD/artifacts/europe/era5/grid.csv
export BIRDCAST_EURO_RUN_DIR=$PWD/artifacts/europe/run/europe-gamm-v1
sbatch deploy/slurm/birdcast-euro-gamm.sbatch
```

The job performs leave-one-radar, country, network and blocked-time tests.
Publication is blocked unless these site-equal gates pass:

- median held-out radar `log1p` skill is positive;
- at least 75% of held-out radar intensity folds have positive skill;
- median top-decile event F1 is at least 0.50;
- median held-out direction error is at most 30 degrees.

Failed models remain research artifacts and cannot be promoted by a favourable
pooled metric.

## Publication

Passing predictions are converted to fixed-grid daily JSON and installed at
the separate `/europe-bird-maps/` route:

```bash
birdcast-uk europe publish \
  --predictions artifacts/europe/run/europe-gamm-v1/predictions_wide_europe.csv \
  --output-root /opt/birdcast-euro/artifacts \
  --model-id europe-gamm-aloft-uk-sp-v1 \
  --aloft-radar-count 152 --uk-sp-radar-count 17 \
  --validation-url validation.json \
  --radars artifacts/europe/source/radars.json

birdcast-uk europe install-site --site-root /opt/birdcast-euro/site
```

The dashboard distinguishes interpolation (within 150 km), extrapolation
(150-250 km), and unsupported cells. Country coastlines are context layers
only and never mask predictions.

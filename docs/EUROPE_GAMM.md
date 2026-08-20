# Europe-wide GAMM

The Europe workflow lives on the repository's single `main` branch. It fits a
source-aware GAMM across the eligible Aloft BALTRAD archive and the immutable
UK SP VPTS archive, with separate validation and publication contracts from
the UK model.

The absolute Europe GAMM is currently withheld because it failed held-out
cross-network transfer gates. The separately labelled radar-relative activity
product may be published; it is not an absolute GAMM prediction.

## Data contract

| Input | Access | Persisted by this workflow |
|---|---|---|
| Aloft coverage and daily VPTS | Streamed from the public Aloft object store | Hourly Parquet and source audit only |
| UK SP VPTS | Read-only from the JASMIN Object Store | Existing hourly UK training rows |
| ERA5 | Europe-specific Bird Maps flow | Site and fixed-grid feature Parquet |
| Raw VP, VPTS and PVOL | Source archives only | Never copied or regenerated |

The model derives an empirical continuous UTC time index and a cyclic UTC-hour
smooth. It applies no hard-coded daylight, twilight, season, or phenology
filter. It uses 200-4000 m profiles, hourly UTC cadence, Aloft BALTRAD as the
reference source, and UK SP as a separately estimated source effect.

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
sbatch --array=0-<model-year-chunk-count-minus-one>%40 deploy/slurm/birdcast-euro-aloft-stream.sbatch
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

Every daily site-feature file is independently reconstructed from its two
immutable daily ERA5 NetCDF files and compared cell-for-cell. GAMM assembly is
blocked unless all 365 raw-ERA5 reconstruction audits pass; status files alone
are not accepted as evidence.

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

## Fidelity And Data-Denial Gates

Before assembling the training table, re-stream every locked radar-month with
the same array range. The verifier compares every compact hourly row and the
source SHA-256, byte count, profile count and hourly count with the immutable
streaming manifest. It writes a compact audit JSON only; raw Aloft files are
never retained.

```bash
sbatch --array=0-13698%40 deploy/slurm/birdcast-euro-fidelity.sbatch

birdcast-uk europe verify-training \
  --training-csv artifacts/europe/training/europe_hourly_era5.csv \
  --transfer-csv artifacts/europe/training/europe_transfer_validation_hourly_era5.csv \
  --cohort artifacts/europe/source/aloft-cohort.json \
  --model-spec configs/gamm_europe_aloft_uk_sp.json \
  --output artifacts/europe/fidelity/training-policy.json
```

The policy check fails on any raw-data persistence declaration, changed source
hash or derived value, unapproved source, UK LP row, transfer-radar leakage,
or missing ERA5 predictor. The GAMM job must not be submitted until every
chunk audit, ERA5 reconstruction audit, and the training-policy report pass.

An advertised VP day whose companion public VPTS object returns HTTP 404 is
written as `unavailable` in its audit, contributes no hourly observation, and
is never imputed as zero migration. Any other source-read error remains fatal.

Freeze `configs/gamm_europe_aloft_uk_sp.json` with an immutable training path
and run identifier, then submit:

The Europe GAMM uses the nine predictor fields shared by the immutable UK SP
model-year table and the independently reconstructed Europe ERA5 site table.
The UK release did not retain 925 or 700 hPa winds, so those optional fields
are intentionally excluded rather than imputed or silently dropped per row.

```bash
export BIRDCAST_EURO_ROOT=$PWD
export BIRDCAST_EURO_RSCRIPT=/gws/smf/j04/ncas_radar/software/miniconda3_radar_group_20200519/envs/R_4_10_biorad_pyart_3_8/bin/Rscript
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

## Research output and public relative-flow product

Passing absolute-model predictions may be converted to fixed-grid daily JSON
for offline research review only:

```bash
birdcast-uk europe publish \
  --predictions artifacts/europe/run/europe-gamm-v1/predictions_wide_europe.csv \
  --output-root artifacts/europe/research-publication \
  --model-id europe-gamm-aloft-uk-sp-v1 \
  --aloft-radar-count 152 --uk-sp-radar-count 17 \
  --validation-url validation.json \
  --radars artifacts/europe/source/radars.json

birdcast-uk europe install-site --site-root artifacts/europe/research-site
```

This command does not deploy to the public host. The former absolute-Europe
Object Store pull/activation path is retired, and its systemd units must remain
absent. The public `/europe-bird-maps/` route serves only the relative activity
and observed-flow product built with `coastal build-relative-flow`.

The dashboard distinguishes interpolation (within 150 km), extrapolation
(150-250 km), and unsupported cells. Country coastlines are context layers
only and never mask predictions.

Before a research manifest may use `release_status: research-validated`, each daily partition
must contain exactly the 24 canonical `00:00`-`23:00Z` frames. The release job
then reconciles every supported prediction with the fixed grid and daily JSON
frames and checks the passed source, ERA5, training, and held-out model
validation reports. Missing, duplicate, unsupported, non-canonical, or altered
map cells deny publication.

The public artifact tree also retains `validation/source-fidelity.json`,
`validation/era5-fidelity.json`, `validation/training-fidelity.json`, and
`validation/model-validation.json`; the manifest links directly to the final
held-out model report.

The live relative-flow builder writes a content-addressed
`archive/relative-flow/<release>/` tree plus `latest/relative-flow.json`.
`birdcast-coastal-activate.sh` verifies the exact schema, cohort dates, archive
manifest, daily asset sizes and hashes, rejects symlinks and unreferenced files,
stages on the destination filesystem, and atomically switches the Nginx data
symlink. A failed build, transfer, or validation cannot alter the public Europe
tab.

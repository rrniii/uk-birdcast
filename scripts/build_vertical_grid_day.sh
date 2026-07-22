#!/bin/bash
set -euo pipefail

# Build one full-coverage ERA5 grid day for a multi-level GAMM experiment.
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the full multi-level raw ERA5 directory}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily grid output directory}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the matching training table}"
: "${BIRDCAST_UK_RADARS_FILE:?Set radar metadata}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${SLURM_ARRAY_TASK_ID:?Run through a Slurm array}"

mapfile -t single_files < <(
  find "$BIRDCAST_UK_ERA5_RAW_DIR" -maxdepth 1 -type f -name 'era5_single_levels_*_uk.nc' -print | sort
)
single="${single_files[$SLURM_ARRAY_TASK_ID]:?array index is outside the ERA5 archive}"
stamp="${single##*_levels_}"
stamp="${stamp%_uk.nc}"
pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
test -f "$pressure"

mkdir -p "$BIRDCAST_UK_ERA5_GRID_DIR"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 grid-features \
  --single-levels "$single" \
  --pressure-levels "$pressure" \
  --radars "$BIRDCAST_UK_RADARS_FILE" \
  --training-table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" \
  --output "$BIRDCAST_UK_ERA5_GRID_DIR/era5_grid_${stamp}.csv"

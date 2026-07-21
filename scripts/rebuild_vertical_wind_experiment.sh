#!/bin/bash
set -euo pipefail

# Rebuild only derived ERA5/VPTS artifacts for a vertical-wind GAMM experiment.
# The raw ERA5 NetCDF and source VPTS archive remain read-only inputs.
: "${BIRDCAST_UK_ROOT:?Set the project root}"
: "${BIRDCAST_UK_EXPERIMENT_DIR:?Set the experiment artifact directory}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

raw_dir="${BIRDCAST_UK_ERA5_RAW_DIR:-$BIRDCAST_UK_ROOT/data/era5/raw}"
site_dir="$BIRDCAST_UK_EXPERIMENT_DIR/site-features"
mkdir -p "$site_dir"

extra_era5_args=()
if [[ -n "${BIRDCAST_UK_EXTRA_ERA5_FEATURES:-}" ]]; then
  IFS=',' read -r -a extra_era5_features <<< "$BIRDCAST_UK_EXTRA_ERA5_FEATURES"
  for feature in "${extra_era5_features[@]}"; do
    extra_era5_args+=(--extra-era5-feature "$feature")
  done
fi

for pressure in "$raw_dir"/era5_pressure_levels_*_uk.nc; do
  stamp="${pressure##*/}"
  stamp="${stamp#era5_pressure_levels_}"
  stamp="${stamp%_uk.nc}"
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 features \
    --single-levels "$raw_dir/era5_single_levels_${stamp}_uk.nc" \
    --pressure-levels "$pressure" \
    --radars "$BIRDCAST_UK_ROOT/data/radars.json" \
    --output "$site_dir/era5_site_features_${stamp}.json"
done

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli features join-era5 \
  --observed-hourly "$BIRDCAST_UK_ROOT/data/reanalysis/vpts-hourly.json" \
  --era5-dir "$site_dir" \
  --output "$BIRDCAST_UK_EXPERIMENT_DIR/model-features.json"

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis prepare \
  --joined-features "$BIRDCAST_UK_EXPERIMENT_DIR/model-features.json" \
  --output "$BIRDCAST_UK_EXPERIMENT_DIR/training-table-full.json" \
  "${extra_era5_args[@]}"

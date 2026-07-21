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

# Vertical predictors are optional by design. Refuse to evaluate a model if
# their availability accidentally reduced the one-year UK training population.
baseline_table="${BIRDCAST_UK_BASELINE_TRAINING_TABLE:-$BIRDCAST_UK_ROOT/data/reanalysis/training.json}"
"$BIRDCAST_UK_PYTHON" - "$baseline_table" "$BIRDCAST_UK_EXPERIMENT_DIR/training-table-full.json" "${BIRDCAST_UK_EXTRA_ERA5_FEATURES:-}" <<'PY'
import json
import sys
from pathlib import Path

baseline = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
vertical = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
required = [item for item in sys.argv[3].split(",") if item]

if vertical["row_count"] != baseline["row_count"]:
    raise SystemExit(
        f"vertical ERA5 table row count {vertical['row_count']} does not match "
        f"baseline {baseline['row_count']}"
    )
if vertical["pulse_counts"] != baseline["pulse_counts"]:
    raise SystemExit("vertical ERA5 table pulse counts do not match the baseline")
missing = sorted(set(required) - set(vertical["feature_columns"]))
if missing:
    raise SystemExit(f"vertical ERA5 table is missing requested features: {', '.join(missing)}")
PY

#!/bin/bash
#SBATCH --job-name=birdcast-uk-grid-reconcile
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-grid-reconcile-%j.out
#SBATCH --error=logs/birdcast-uk-grid-reconcile-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw archive directory}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily national grid directory}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the prepared model table}"
: "${BIRDCAST_UK_RADARS_FILE:?Set radar metadata}"

module load jaspy/3.12/v20250704
end_date="$("$BIRDCAST_UK_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["window"]["end_date"])' "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY")"
mkdir -p "$BIRDCAST_UK_ERA5_GRID_DIR"

repaired=0
for offset in $(seq 0 364); do
  day="$(date -u -d "${end_date} -${offset} days" +%Y-%m-%d)"
  stamp="${day//-/}"
  single="$BIRDCAST_UK_ERA5_RAW_DIR/era5_single_levels_${stamp}_uk.nc"
  pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
  output="$BIRDCAST_UK_ERA5_GRID_DIR/era5_grid_${stamp}.csv"
  if [ -s "$output" ] && [ "$(wc -l < "$output")" -eq 12841 ]; then
    continue
  fi
  test -s "$single"
  test -s "$pressure"
  temporary="${output}.tmp"
  rm -f "$temporary"
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 grid-features \
    --single-levels "$single" \
    --pressure-levels "$pressure" \
    --radars "$BIRDCAST_UK_RADARS_FILE" \
    --training-table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" \
    --output "$temporary"
  test "$(wc -l < "$temporary")" -eq 12841
  mv "$temporary" "$output"
  repaired=$((repaired + 1))
done

for output in "$BIRDCAST_UK_ERA5_GRID_DIR"/era5_grid_*.csv; do
  test "$(wc -l < "$output")" -eq 12841
done
test "$(find "$BIRDCAST_UK_ERA5_GRID_DIR" -maxdepth 1 -name 'era5_grid_*.csv' -print | wc -l)" -eq 365
printf 'ERA5 grid reconciliation complete: repaired=%s window_days=365\n' "$repaired"

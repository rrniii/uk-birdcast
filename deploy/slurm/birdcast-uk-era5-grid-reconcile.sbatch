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

valid_grid() {
  output="$1"
  status="${output}.status.json"
  [ -s "$output" ] && [ -s "$status" ] || return 1
  expected_lines="$("$BIRDCAST_UK_PYTHON" -c 'import json,sys; p=json.load(open(sys.argv[1])); assert p.get("domain_policy") == "union_of_radar_ranges"; assert p.get("boundary") is None; print(int(p["row_count"]) + 1)' "$status")" || return 1
  [ "$(wc -l < "$output")" -eq "$expected_lines" ]
}

repaired=0
for offset in $(seq 0 364); do
  day="$(date -u -d "${end_date} -${offset} days" +%Y-%m-%d)"
  stamp="${day//-/}"
  single="$BIRDCAST_UK_ERA5_RAW_DIR/era5_single_levels_${stamp}_uk.nc"
  pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
  output="$BIRDCAST_UK_ERA5_GRID_DIR/era5_grid_${stamp}.csv"
  temporary="${output}.tmp"
  if valid_grid "$output"; then
    rm -f "$temporary" "${temporary}.status.json"
    continue
  fi
  test -s "$single"
  test -s "$pressure"
  rm -f "$temporary" "${temporary}.status.json"
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 grid-features \
    --single-levels "$single" \
    --pressure-levels "$pressure" \
    --radars "$BIRDCAST_UK_RADARS_FILE" \
    --training-table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" \
    --output "$temporary"
  valid_grid "$temporary"
  mv "$temporary" "$output"
  mv "${temporary}.status.json" "${output}.status.json"
  repaired=$((repaired + 1))
done

for output in "$BIRDCAST_UK_ERA5_GRID_DIR"/era5_grid_*.csv; do
  valid_grid "$output"
done
test "$(find "$BIRDCAST_UK_ERA5_GRID_DIR" -maxdepth 1 -name 'era5_grid_*.csv' -print | wc -l)" -eq 365
printf 'ERA5 grid reconciliation complete: repaired=%s window_days=365\n' "$repaired"

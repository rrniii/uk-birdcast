#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-grid
#SBATCH --time=08:00:00
#SBATCH --cpus-per-task=4
#SBATCH --mem=24G
#SBATCH --output=logs/birdcast-uk-era5-grid-%j.out
#SBATCH --error=logs/birdcast-uk-era5-grid-%j.err

set -euo pipefail

: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw archive directory}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily national ERA5 grid output directory}"
: "${BIRDCAST_UK_ERA5_GRID_TABLE:?Set the merged national ERA5 grid CSV path}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the prepared rolling model table}"
: "${BIRDCAST_UK_RADARS_FILE:?Set radar metadata}"
: "${BIRDCAST_UK_ROOT:?Set the standalone repository path}"
: "${BIRDCAST_UK_ERA5_CONTAINER:?Set the Singularity ERA5 image path}"

mkdir -p "$BIRDCAST_UK_ERA5_GRID_DIR"
export SINGULARITY_BINDPATH="${SINGULARITY_BINDPATH:-/gws}"
for single in "$BIRDCAST_UK_ERA5_RAW_DIR"/era5_single_levels_*_uk.nc; do
  [ -f "$single" ] || continue
  stamp="${single##*_levels_}"
  stamp="${stamp%_uk.nc}"
  pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
  [ -f "$pressure" ] || continue
  singularity exec --bind "$BIRDCAST_UK_ROOT:/app" "$BIRDCAST_UK_ERA5_CONTAINER" python3 -m birdcast_uk.cli era5 grid-features --single-levels "$single" --pressure-levels "$pressure" --radars "$BIRDCAST_UK_RADARS_FILE" --training-table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" --output "$BIRDCAST_UK_ERA5_GRID_DIR/era5_grid_${stamp}.csv"
done

first="$(find "$BIRDCAST_UK_ERA5_GRID_DIR" -maxdepth 1 -name 'era5_grid_*.csv' -print | sort | head -1)"
[ -n "$first" ]
head -1 "$first" > "$BIRDCAST_UK_ERA5_GRID_TABLE"
for source in "$BIRDCAST_UK_ERA5_GRID_DIR"/era5_grid_*.csv; do tail -n +2 "$source" >> "$BIRDCAST_UK_ERA5_GRID_TABLE"; done

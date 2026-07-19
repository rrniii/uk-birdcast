#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-grid-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=logs/birdcast-uk-era5-grid-merge-%j.out
#SBATCH --error=logs/birdcast-uk-era5-grid-merge-%j.err

set -euo pipefail

: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily national ERA5 grid output directory}"
: "${BIRDCAST_UK_ERA5_GRID_TABLE:?Set the merged national ERA5 grid CSV path}"

first="$(find "$BIRDCAST_UK_ERA5_GRID_DIR" -maxdepth 1 -name 'era5_grid_*.csv' -print | sort | head -1)"
[ -n "$first" ]
mkdir -p "$(dirname "$BIRDCAST_UK_ERA5_GRID_TABLE")"
temporary="${BIRDCAST_UK_ERA5_GRID_TABLE}.tmp"
head -1 "$first" > "$temporary"
for source in "$BIRDCAST_UK_ERA5_GRID_DIR"/era5_grid_*.csv; do tail -n +2 "$source" >> "$temporary"; done
mv "$temporary" "$BIRDCAST_UK_ERA5_GRID_TABLE"

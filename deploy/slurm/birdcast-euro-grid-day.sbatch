#!/bin/bash
# Derive one land-and-water Europe model grid from validated ERA5 and training support.
#SBATCH --job-name=birdcast-euro-grid
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --output=logs/birdcast-euro-grid-%A_%a.out
#SBATCH --error=logs/birdcast-euro-grid-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_RAW_ERA5_ROOT:?Set the Europe ERA5 raw root}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata}"
: "${BIRDCAST_EURO_TRAINING_CONTRACT:?Set the passed Europe training contract}"
: "${BIRDCAST_EURO_GRID_DAILY_ROOT:?Set the Europe daily grid output root}"
: "${SLURM_ARRAY_TASK_ID:?Submit as a 0-364 array}"

if [ "$SLURM_ARRAY_TASK_ID" -lt 0 ] || [ "$SLURM_ARRAY_TASK_ID" -gt 364 ]; then
  echo "Europe grid array index must be in 0..364" >&2
  exit 2
fi

module load jaspy/3.12/v20250704
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
day="$(date -u -d "2025-07-14 + ${SLURM_ARRAY_TASK_ID} day" +%F)"
stamp="${day//-/}"
mkdir -p "$BIRDCAST_EURO_GRID_DAILY_ROOT"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli era5 grid-features \
  --single-levels "$BIRDCAST_EURO_RAW_ERA5_ROOT/era5_single_levels_${stamp}_uk.nc" \
  --pressure-levels "$BIRDCAST_EURO_RAW_ERA5_ROOT/era5_pressure_levels_${stamp}_uk.nc" \
  --radars "$BIRDCAST_EURO_RADARS" --training-table "$BIRDCAST_EURO_TRAINING_CONTRACT" \
  --output "$BIRDCAST_EURO_GRID_DAILY_ROOT/era5_grid_${stamp}.parquet"

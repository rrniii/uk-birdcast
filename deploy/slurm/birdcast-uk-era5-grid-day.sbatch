#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-grid-day
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --array=0-364%4
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-era5-grid-%A_%a.out
#SBATCH --error=logs/birdcast-uk-era5-grid-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw archive directory}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily national ERA5 grid output directory}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the prepared rolling model table}"
: "${BIRDCAST_UK_RADARS_FILE:?Set radar metadata}"
: "${BIRDCAST_UK_BOUNDARY:?Set the high-resolution UK boundary path}"

module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli historical boundary --output "$BIRDCAST_UK_BOUNDARY"
day="$("$BIRDCAST_UK_PYTHON" -c 'import datetime,json,sys; window=json.load(open(sys.argv[1]))["window"]; end=datetime.datetime.strptime(window["end_date"], "%Y%m%d").date(); print((end-datetime.timedelta(days=int(sys.argv[2]))).isoformat())' "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY" "${SLURM_ARRAY_TASK_ID}")"
stamp="${day//-/}"
single="$BIRDCAST_UK_ERA5_RAW_DIR/era5_single_levels_${stamp}_uk.nc"
pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
[ -f "$single" ] && [ -f "$pressure" ]
mkdir -p "$BIRDCAST_UK_ERA5_GRID_DIR"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 grid-features \
  --single-levels "$single" --pressure-levels "$pressure" \
  --radars "$BIRDCAST_UK_RADARS_FILE" \
  --training-table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" \
  --boundary "$BIRDCAST_UK_BOUNDARY" \
  --output "$BIRDCAST_UK_ERA5_GRID_DIR/era5_grid_${stamp}.csv"

#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-backfill
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --array=0-364%4
#SBATCH --time=03:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-era5-backfill-%A_%a.out
#SBATCH --error=logs/birdcast-uk-era5-backfill-%A_%a.err

set -euo pipefail

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw directory}"
: "${BIRDCAST_UK_ERA5_FEATURE_DIR:?Set the ERA5 site-feature directory}"
: "${BIRDCAST_UK_RADARS_FILE:?Set the radar metadata path}"

module load jaspy/3.12/v20250704
day="$("$BIRDCAST_UK_PYTHON" -c 'import datetime,json,sys; window=json.load(open(sys.argv[1]))["window"]; end=datetime.datetime.strptime(window["end_date"], "%Y%m%d").date(); print((end-datetime.timedelta(days=int(sys.argv[2]))).isoformat())' "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY" "${SLURM_ARRAY_TASK_ID}")"
stamp="${day//-/}"
mkdir -p "$BIRDCAST_UK_ERA5_RAW_DIR" "$BIRDCAST_UK_ERA5_FEATURE_DIR"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 build-day --day "$day" \
  --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
  --feature-output "$BIRDCAST_UK_ERA5_FEATURE_DIR/era5_site_features_${stamp}.json" \
  --radars "$BIRDCAST_UK_RADARS_FILE" --download

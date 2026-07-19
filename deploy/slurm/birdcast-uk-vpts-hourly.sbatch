#!/bin/bash
#SBATCH --job-name=birdcast-uk-vpts-hourly
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --output=logs/birdcast-uk-vpts-hourly-%j.out
#SBATCH --error=logs/birdcast-uk-vpts-hourly-%j.err

set -euo pipefail

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_OBSERVED_HOURLY:?Set the hourly output path}"

module load jaspy/3.12/v20250704
mkdir -p "$(dirname "$BIRDCAST_UK_OBSERVED_HOURLY")"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli observed hourly \
  --input "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY" --output "$BIRDCAST_UK_OBSERVED_HOURLY"

#!/bin/bash
#SBATCH --job-name=birdcast-uk-reanalysis-prepare
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:30:00
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-uk-reanalysis-prepare-%j.out
#SBATCH --error=logs/birdcast-uk-reanalysis-prepare-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
set -u
: "${BIRDCAST_UK_MODEL_FEATURES:?Set the joined hourly VPTS/ERA5 source}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the rolling model table path}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis prepare --joined-features "$BIRDCAST_UK_MODEL_FEATURES" --output "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE"

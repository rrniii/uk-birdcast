#!/bin/bash
#SBATCH --job-name=birdcast-uk-reanalysis-prepare
#SBATCH --time=00:30:00
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-uk-reanalysis-prepare-%j.out
#SBATCH --error=logs/birdcast-uk-reanalysis-prepare-%j.err

set -euo pipefail
: "${BIRDCAST_UK_MODEL_FEATURES:?Set the joined hourly VPTS/ERA5 source}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the rolling model table path}"
birdcast-uk reanalysis prepare --joined-features "$BIRDCAST_UK_MODEL_FEATURES" --output "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE"

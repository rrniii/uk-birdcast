#!/bin/bash
#SBATCH --job-name=birdcast-uk-feature-join
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:30:00
#SBATCH --mem=8G
#SBATCH --output=logs/birdcast-uk-feature-join-%j.out
#SBATCH --error=logs/birdcast-uk-feature-join-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_OBSERVED_HOURLY:?Set the hourly VPTS source}"
: "${BIRDCAST_UK_ERA5_FEATURE_DIR:?Set the ERA5 site-feature directory}"
: "${BIRDCAST_UK_MODEL_FEATURES:?Set the joined model-feature output}"

module load jaspy/3.12/v20250704
mkdir -p "$(dirname "$BIRDCAST_UK_MODEL_FEATURES")"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli join era5 \
  --observed-hourly "$BIRDCAST_UK_OBSERVED_HOURLY" \
  --era5-dir "$BIRDCAST_UK_ERA5_FEATURE_DIR" \
  --output "$BIRDCAST_UK_MODEL_FEATURES"

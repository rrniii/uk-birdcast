#!/bin/bash
#SBATCH --job-name=birdcast-uk-gamm
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=48G
#SBATCH --output=logs/birdcast-uk-gamm-%j.out
#SBATCH --error=logs/birdcast-uk-gamm-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ROOT:?Set the standalone repository path}"
: "${BIRDCAST_UK_ERA5_GRID_TABLE:?Set the hourly national ERA5 grid CSV}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the rolling model table}"
: "${BIRDCAST_UK_REANALYSIS_RUN_DIR:?Set the immutable model run directory}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

mkdir -p "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm"
gamm_options="${BIRDCAST_UK_GAMM_OPTIONS:-$BIRDCAST_UK_ROOT/configs/gamm_uk_holdout_selected.json}"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis spec \
  --table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" \
  --model-family gamm \
  --gamm-options "$gamm_options" \
  --output "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm-spec.json"
module load jasr/4.4/v20250704
Rscript "$BIRDCAST_UK_ROOT/scripts/fit_gamm.R" \
  "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm-spec.json" \
  "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm" \
  "$BIRDCAST_UK_ERA5_GRID_TABLE"

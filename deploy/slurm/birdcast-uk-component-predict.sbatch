#!/bin/bash
#SBATCH --job-name=birdcast-uk-component-predict
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-component-predict-%A_%a.out
#SBATCH --error=logs/birdcast-uk-component-predict-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the reviewed release SHA}"
: "${BIRDCAST_UK_PYTHON:?Set the release-local Python executable}"
: "${BIRDCAST_UK_COMPONENT_MANIFEST:?Set the immutable component manifest}"
: "${BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256:?Set the reviewed manifest hash}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the validated daily ERA5 grid directory}"
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set a private prediction directory}"

module load "${BIRDCAST_UK_R_MODULE:-jasr/4.4/v20250704}"
export PYTHONPATH="$BIRDCAST_UK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
"$BIRDCAST_UK_ROOT/scripts/predict_gamm_components_day.sh"

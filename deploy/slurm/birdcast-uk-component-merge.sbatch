#!/bin/bash
#SBATCH --job-name=birdcast-uk-component-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=logs/birdcast-uk-component-merge-%j.out
#SBATCH --error=logs/birdcast-uk-component-merge-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the reviewed release SHA}"
: "${BIRDCAST_UK_PYTHON:?Set the release-local Python executable}"
: "${BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256:?Set the reviewed manifest hash}"
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set the validated daily predictions}"
: "${BIRDCAST_UK_COMPONENT_MERGED_DIR:?Set a private merge directory}"
: "${BIRDCAST_UK_EXPECTED_START_DAY:?Set the validated first model day}"
: "${BIRDCAST_UK_EXPECTED_END_DAY:?Set the validated last model day}"

module load jaspy/3.12/v20250704
export PYTHONPATH="$BIRDCAST_UK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
"$BIRDCAST_UK_ROOT/scripts/merge_gamm_component_predictions_job.sh"

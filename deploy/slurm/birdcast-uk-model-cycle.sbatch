#!/bin/bash
#SBATCH --job-name=birdcast-uk-model-cycle
#SBATCH --account=ncas_radar
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
module load jaspy/3.12/v20250704
module load jasr/4.4/v20250704
set -u
umask 077
export PYTHONPATH="$BIRDCAST_UK_ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
export PATH="$BIRDCAST_UK_ROOT/.venv/bin:$PATH"
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.model_cycle \
  --state-root "${BIRDCAST_UK_MODEL_STATE:?}" \
  --radars "${BIRDCAST_UK_RADARS_FILE:?}" \
  --training-table "${BIRDCAST_UK_TRAINING_TABLE:?}" \
  --component-manifest "${BIRDCAST_UK_COMPONENT_MANIFEST:?}" \
  --predict-script "$BIRDCAST_UK_ROOT/scripts/predict_gamm_components.R" \
  --release-sha "$BIRDCAST_UK_RELEASE_SHA" \
  --public-base-url "${BIRDCAST_UK_PUBLIC_BASE_URL:?}" \
  --object-prefix "${BIRDCAST_UK_OBJECT_PREFIX:?}" \
  --bucket "${BIRDCAST_UK_OBJECT_STORE_BUCKET:?}" \
  --s3cmd-config "${BIRDCAST_UK_S3CMD_CONFIG:?}" \
  --lag-days "${BIRDCAST_UK_MODEL_LAG_DAYS:-6}" \
  --max-days "${BIRDCAST_UK_MODEL_MAX_DAYS:-60}" --publish

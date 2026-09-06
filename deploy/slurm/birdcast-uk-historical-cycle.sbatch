#!/bin/bash
#SBATCH --job-name=birdcast-uk-historical-cycle
#SBATCH --account=ncas_radar
#SBATCH --partition=standard
#SBATCH --qos=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=24G

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
module load jaspy/3.12/v20250704
set -u
umask 077
export PYTHONPATH="$BIRDCAST_UK_ROOT/src"
export PYTHONDONTWRITEBYTECODE=1
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.historical_cycle \
  --input-root "${BIRDCAST_UK_VPTS_LOCAL_ROOT:?}" \
  --state-root "${BIRDCAST_UK_CYCLE_STATE:?}" \
  --radars "${BIRDCAST_UK_RADARS_FILE:?}" \
  --boundary "${BIRDCAST_UK_BOUNDARY:?}" \
  --catalog-url "${BIRDCAST_UK_VPTS_CATALOG_URL:?}" \
  --public-base-url "${BIRDCAST_UK_PUBLIC_BASE_URL:?}" \
  --object-prefix "${BIRDCAST_UK_OBJECT_PREFIX:?}" \
  --bucket "${BIRDCAST_UK_OBJECT_STORE_BUCKET:?}" \
  --s3cmd-config "${BIRDCAST_UK_S3CMD_CONFIG:?}" \
  --workers "${SLURM_CPUS_PER_TASK:-8}" --publish

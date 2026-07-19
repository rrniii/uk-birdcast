#!/bin/bash
#SBATCH --job-name=birdcast-uk-publish
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-uk-publish-%j.out
#SBATCH --error=logs/birdcast-uk-publish-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ARTIFACT_ROOT:?Set the artifact root}"
: "${BIRDCAST_UK_OBJECT_PREFIX:?Set the object prefix}"
: "${BIRDCAST_UK_OBJECT_STORE_BUCKET:?Set the Object Store bucket}"
: "${BIRDCAST_UK_OBJECT_STORE_PLAN:?Set the publication plan path}"
: "${BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT:?Set the generated sync script path}"
: "${BIRDCAST_UK_PUBLIC_BASE_URL:?Set the public Object Store base URL}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_RADARS_FILE:?Set the radar metadata path}"
: "${BIRDCAST_UK_S3CMD_CONFIG:?Set the private s3cmd configuration path}"

module load jaspy/3.12/v20250704
mkdir -p "$(dirname "$BIRDCAST_UK_OBJECT_STORE_PLAN")"

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli static build \
  --output-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --public-base-url "$BIRDCAST_UK_PUBLIC_BASE_URL" \
  --object-prefix "$BIRDCAST_UK_OBJECT_PREFIX" \
  --radars "$BIRDCAST_UK_RADARS_FILE"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli publish plan \
  --source-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --output "$BIRDCAST_UK_OBJECT_STORE_PLAN" \
  --object-prefix "$BIRDCAST_UK_OBJECT_PREFIX"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli publish sync-script \
  --plan "$BIRDCAST_UK_OBJECT_STORE_PLAN" \
  --output "$BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT" \
  --bucket "$BIRDCAST_UK_OBJECT_STORE_BUCKET" \
  --client s3cmd \
  --s3cmd-config "$BIRDCAST_UK_S3CMD_CONFIG"

/bin/sh "$BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT"

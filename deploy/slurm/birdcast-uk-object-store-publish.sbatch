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
: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the reviewed release SHA}"
: "${BIRDCAST_UK_OBJECT_PREFIX:?Set the object prefix}"
: "${BIRDCAST_UK_OBJECT_STORE_BUCKET:?Set the Object Store bucket}"
: "${BIRDCAST_UK_OBJECT_STORE_PLAN:?Set the publication plan path}"
: "${BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT:?Set the generated sync script path}"
: "${BIRDCAST_UK_PUBLIC_BASE_URL:?Set the public Object Store base URL}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_RADARS_FILE:?Set the radar metadata path}"
: "${BIRDCAST_UK_S3CMD_CONFIG:?Set the private s3cmd configuration path}"

module load jaspy/3.12/v20250704
export PYTHONPATH="$BIRDCAST_UK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
mkdir -p "$(dirname "$BIRDCAST_UK_OBJECT_STORE_PLAN")"
plan_path="${BIRDCAST_UK_OBJECT_STORE_PLAN}.${SLURM_JOB_ID:?}"
sync_script="${BIRDCAST_UK_OBJECT_STORE_SYNC_SCRIPT}.${SLURM_JOB_ID}"
read -r -a publication_products <<< "${BIRDCAST_UK_PUBLICATION_PRODUCTS:-historical gam-era5}"
validate_arguments=()
plan_arguments=()
for product in "${publication_products[@]}"; do
  case "$product" in
    historical|gam-era5) ;;
    *) echo "Unsupported publication product: $product" >&2; exit 1 ;;
  esac
  validate_arguments+=(--require "$product")
  plan_arguments+=(--product "$product")
done
if [ "${#publication_products[@]}" -eq 0 ]; then
  echo "At least one publication product is required" >&2
  exit 1
fi

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli static build \
  --output-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --public-base-url "$BIRDCAST_UK_PUBLIC_BASE_URL" \
  --object-prefix "$BIRDCAST_UK_OBJECT_PREFIX" \
  --radars "$BIRDCAST_UK_RADARS_FILE"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli publish validate \
  --source-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  "${validate_arguments[@]}"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli publish plan \
  --source-dir "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --output "$plan_path" \
  --object-prefix "$BIRDCAST_UK_OBJECT_PREFIX" \
  "${plan_arguments[@]}"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli publish sync-script \
  --plan "$plan_path" \
  --output "$sync_script" \
  --bucket "$BIRDCAST_UK_OBJECT_STORE_BUCKET" \
  --client s3cmd \
  --s3cmd-config "$BIRDCAST_UK_S3CMD_CONFIG"

/bin/sh "$sync_script"

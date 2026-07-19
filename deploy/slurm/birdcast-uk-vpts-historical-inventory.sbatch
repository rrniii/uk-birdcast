#!/bin/bash
#SBATCH --job-name=birdcast-uk-vpts-archive
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-uk-vpts-archive-%j.out
#SBATCH --error=logs/birdcast-uk-vpts-archive-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_RADARS_FILE:?Set the radar metadata path}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_VPTS_CATALOG_URL:?Set the VPTS catalogue URL}"
: "${BIRDCAST_UK_PUBLIC_BASE_URL:?Set the public Object Store base URL}"
: "${BIRDCAST_UK_OBJECT_STORE_BUCKET:?Set the Object Store bucket}"

module load jaspy/3.12/v20250704
mkdir -p "$(dirname "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY")" "$(dirname "$BIRDCAST_UK_RADARS_FILE")"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli radars from-pvol-catalog --output "$BIRDCAST_UK_RADARS_FILE"
inventory_args=(
  --output "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY"
  --catalog-url "$BIRDCAST_UK_VPTS_CATALOG_URL"
  --public-base-url "$BIRDCAST_UK_PUBLIC_BASE_URL"
  --bucket "$BIRDCAST_UK_OBJECT_STORE_BUCKET"
  --days 365
  --max-workers 16
)
if [ -n "${BIRDCAST_UK_REANALYSIS_END_DATE:-}" ]; then
  inventory_args+=(--end-date "$BIRDCAST_UK_REANALYSIS_END_DATE")
fi
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli vpts historical-inventory \
  "${inventory_args[@]}"

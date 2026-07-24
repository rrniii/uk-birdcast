#!/bin/bash
# One calendar-month Europe ERA5 request, split to daily NetCDF and site data.
# Submit 0-12: 14-31 Jul 2025, Aug 2025-Jun 2026, and 1-13 Jul 2026.
#SBATCH --job-name=birdcast-euro-era5
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=24G
#SBATCH --output=logs/birdcast-euro-era5-%A_%a.out
#SBATCH --error=logs/birdcast-euro-era5-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_RAW_ERA5_ROOT:?Set the Europe ERA5 raw root}"
: "${BIRDCAST_EURO_SITE_ERA5_ROOT:?Set the Europe ERA5 site-feature root}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata}"
: "${SLURM_ARRAY_TASK_ID:?Submit as an array from 0 to 12}"

module load jaspy/3.12/v20250704
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
index="$SLURM_ARRAY_TASK_ID"
if [ "$index" -gt 12 ]; then
  echo "Europe ERA5 array index must be in 0..12" >&2
  exit 2
fi
month_start=$(date -u -d "2025-07-01 + ${index} month" +%F)
month_end=$(date -u -d "${month_start} +1 month -1 day" +%F)
if [ "$index" -eq 0 ]; then month_start=2025-07-14; fi
if [ "$index" -eq 12 ]; then month_end=2026-07-13; fi

"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli era5 build-period \
  --start-day "$month_start" --end-day "$month_end" \
  --raw-dir "$BIRDCAST_EURO_RAW_ERA5_ROOT" \
  --feature-dir "$BIRDCAST_EURO_SITE_ERA5_ROOT" \
  --radars "$BIRDCAST_EURO_RADARS" \
  --domain europe --download

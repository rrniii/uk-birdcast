#!/bin/bash
# Independently reconstruct one Europe ERA5 day from raw NetCDF inputs.
#SBATCH --job-name=birdcast-euro-era5-fidelity
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --output=logs/birdcast-euro-era5-fidelity-%A_%a.out
#SBATCH --error=logs/birdcast-euro-era5-fidelity-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
: "${BIRDCAST_EURO_RAW_ERA5_ROOT:?Set the Europe ERA5 raw root}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata}"
: "${BIRDCAST_EURO_SITE_ERA5_ROOT:?Set the Europe site-feature root}"
: "${BIRDCAST_EURO_ERA5_FIDELITY_ROOT:?Set the Europe ERA5 audit output root}"
: "${SLURM_ARRAY_TASK_ID:?Submit as a 0-364 array}"

if [ "$SLURM_ARRAY_TASK_ID" -lt 0 ] || [ "$SLURM_ARRAY_TASK_ID" -gt 364 ]; then
  echo "Europe ERA5 fidelity array index must be in 0..364" >&2
  exit 2
fi
module load jaspy/3.12/v20250704
day="$(date -u -d "2025-07-14 + ${SLURM_ARRAY_TASK_ID} day" +%F)"
stamp="${day//-/}"
mkdir -p "$BIRDCAST_EURO_ERA5_FIDELITY_ROOT"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/verify_europe_era5_day.py" \
  --day "$day" --raw-dir "$BIRDCAST_EURO_RAW_ERA5_ROOT" --radars "$BIRDCAST_EURO_RADARS" \
  --feature-output "$BIRDCAST_EURO_SITE_ERA5_ROOT/era5_site_features_${stamp}.json" \
  --output "$BIRDCAST_EURO_ERA5_FIDELITY_ROOT/${stamp}.json"

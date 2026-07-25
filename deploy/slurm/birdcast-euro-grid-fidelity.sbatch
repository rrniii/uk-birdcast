#!/bin/bash
# Independently reconstruct one Europe ERA5 grid day before model prediction.
#SBATCH --job-name=birdcast-euro-grid-fidelity
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --output=logs/birdcast-euro-grid-fidelity-%A_%a.out
#SBATCH --error=logs/birdcast-euro-grid-fidelity-%A_%a.err

set -euo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
: "${BIRDCAST_EURO_ROOT:?Set immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set branch Python executable}"
: "${BIRDCAST_EURO_RELEASE:?Set Europe release identifier}"
: "${BIRDCAST_EURO_RAW_ERA5_ROOT:?Set raw ERA5 root}"
: "${BIRDCAST_EURO_RADARS:?Set radar metadata}"
: "${BIRDCAST_EURO_TRAINING_CONTRACT:?Set passed training contract}"
: "${BIRDCAST_EURO_GRID_DAILY_ROOT:?Set daily grid root}"
: "${BIRDCAST_EURO_GRID_FIDELITY_ROOT:?Set grid fidelity root}"
: "${SLURM_ARRAY_TASK_ID:?Submit as a 0-364 array}"
if [ "$SLURM_ARRAY_TASK_ID" -lt 0 ] || [ "$SLURM_ARRAY_TASK_ID" -gt 364 ]; then
  echo "Europe grid fidelity array index must be in 0..364" >&2
  exit 2
fi
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
day="$(date -u -d "2025-07-14 + ${SLURM_ARRAY_TASK_ID} day" +%F)"
stamp="${day//-/}"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/verify_europe_grid_day.py" \
  --day "$day" --raw-dir "$BIRDCAST_EURO_RAW_ERA5_ROOT" --radars "$BIRDCAST_EURO_RADARS" \
  --training-contract "$BIRDCAST_EURO_TRAINING_CONTRACT" \
  --grid-output "$BIRDCAST_EURO_GRID_DAILY_ROOT/era5_grid_${stamp}.parquet" \
  --output "$BIRDCAST_EURO_GRID_FIDELITY_ROOT/${stamp}.json" --release-id "$BIRDCAST_EURO_RELEASE"

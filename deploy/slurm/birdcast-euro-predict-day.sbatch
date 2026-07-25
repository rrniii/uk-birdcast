#!/bin/bash
# Predict one model-year UTC day from audited grid features and a passed GAMM.
#SBATCH --job-name=birdcast-euro-predict
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=32G
#SBATCH --output=logs/birdcast-euro-predict-%A_%a.out
#SBATCH --error=logs/birdcast-euro-predict-%A_%a.err

set -euo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_RSCRIPT:?Set the pinned JASMIN Rscript executable}"
: "${BIRDCAST_EURO_GRID_DAILY_ROOT:?Set audited Europe daily grid root}"
: "${BIRDCAST_EURO_MODEL_RUN_DIR:?Set the passed Europe GAMM run directory}"
: "${BIRDCAST_EURO_PREDICTION_DAILY_ROOT:?Set daily prediction output root}"
: "${BIRDCAST_EURO_MODEL_VALIDATION:?Set passed GAMM validation report}"
: "${SLURM_ARRAY_TASK_ID:?Submit as a 0-364 array}"

if [ "$SLURM_ARRAY_TASK_ID" -lt 0 ] || [ "$SLURM_ARRAY_TASK_ID" -gt 364 ]; then
  echo "Europe prediction array index must be in 0..364" >&2
  exit 2
fi
"$BIRDCAST_EURO_PYTHON" -c 'import json,sys; sys.exit(0 if json.load(open(sys.argv[1])).get("release_passed") is True else 1)' "$BIRDCAST_EURO_MODEL_VALIDATION"
day="$(date -u -d "2025-07-14 + ${SLURM_ARRAY_TASK_ID} day" +%F)"
stamp="${day//-/}"
input="$BIRDCAST_EURO_GRID_DAILY_ROOT/era5_grid_${stamp}.parquet"
test -s "$input"
mkdir -p "$BIRDCAST_EURO_PREDICTION_DAILY_ROOT"
workdir="${TMPDIR:-/tmp}/birdcast-euro-${SLURM_JOB_ID}-${SLURM_ARRAY_TASK_ID}"
mkdir -p "$workdir"
trap 'rm -rf "$workdir"' EXIT
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/convert_europe_grid_day.py" --source "$input" --output "$workdir/grid.csv"
"$BIRDCAST_EURO_RSCRIPT" "$BIRDCAST_EURO_ROOT/scripts/predict_europe_gamm_day.R" "$BIRDCAST_EURO_MODEL_RUN_DIR" "$workdir/grid.csv" "$BIRDCAST_EURO_PREDICTION_DAILY_ROOT/prediction_${stamp}.csv.gz"

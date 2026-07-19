#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=48G
#SBATCH --output=logs/birdcast-uk-reanalysis-%j.out
#SBATCH --error=logs/birdcast-uk-reanalysis-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
set -u

: "${BIRDCAST_UK_ROOT:?Set the standalone repository path}"
: "${BIRDCAST_UK_ARTIFACT_ROOT:?Set the published artifact root}"
: "${BIRDCAST_UK_MODEL_FEATURES:?Set the joined hourly VPTS/ERA5 source}"
: "${BIRDCAST_UK_ERA5_GRID_TABLE:?Set the hourly national ERA5 grid CSV with support scores}"
: "${BIRDCAST_UK_REANALYSIS_TRAINING_TABLE:?Set the reusable rolling model table path}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

run_dir="${BIRDCAST_UK_ARTIFACT_ROOT}/reanalysis/run-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$run_dir" "${BIRDCAST_UK_ARTIFACT_ROOT}/logs"

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis spec --table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" --model-family gamm --output "$run_dir/gamm-spec.json"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis spec --table "$BIRDCAST_UK_REANALYSIS_TRAINING_TABLE" --model-family xgboost --output "$run_dir/xgboost-spec.json"

module load jasr/4.4/v20250704
Rscript "$BIRDCAST_UK_ROOT/scripts/fit_gamm.R" "$run_dir/gamm-spec.json" "$run_dir/gamm" "$BIRDCAST_UK_ERA5_GRID_TABLE"
module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" "$BIRDCAST_UK_ROOT/scripts/fit_xgboost.py" "$run_dir/xgboost-spec.json" "$run_dir/xgboost" "$BIRDCAST_UK_ERA5_GRID_TABLE"

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis compare --gamm-metrics "$run_dir/gamm/metrics.json" --xgboost-metrics "$run_dir/xgboost/metrics.json" --output "$run_dir/model-selection.json"

selected="$("$BIRDCAST_UK_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_model_family"])' "$run_dir/model-selection.json")"
if [ "$selected" = gamm ]; then
  predictions="$run_dir/gamm/predictions_long.csv"
else
  first_prediction="$(find "$run_dir/xgboost" -maxdepth 1 -name 'prediction_*.csv' -print | sort | head -1)"
  [ -n "$first_prediction" ]
  head -1 "$first_prediction" > "$run_dir/xgboost/predictions_long.csv"
  for prediction in "$run_dir"/xgboost/prediction_*.csv; do tail -n +2 "$prediction" >> "$run_dir/xgboost/predictions_long.csv"; done
  predictions="$run_dir/xgboost/predictions_long.csv"
fi
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis frames --predictions-csv "$predictions" --model-family "$selected" --output "$run_dir/predictions.json"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli historical boundary --output "$BIRDCAST_UK_ARTIFACT_ROOT/assets/uk-boundary.geojson"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis publish --predictions "$run_dir/predictions.json" --comparison "$run_dir/model-selection.json" --output-root "$BIRDCAST_UK_ARTIFACT_ROOT"

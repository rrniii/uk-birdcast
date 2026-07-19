#!/bin/bash
#SBATCH --job-name=birdcast-uk-model-select
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-model-select-%j.out
#SBATCH --error=logs/birdcast-uk-model-select-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ARTIFACT_ROOT:?Set the published artifact root}"
: "${BIRDCAST_UK_REANALYSIS_RUN_DIR:?Set the immutable model run directory}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis compare \
  --gamm-metrics "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm/metrics.json" \
  --xgboost-metrics "$BIRDCAST_UK_REANALYSIS_RUN_DIR/xgboost/metrics.json" \
  --output "$BIRDCAST_UK_REANALYSIS_RUN_DIR/model-selection.json"

selected="$("$BIRDCAST_UK_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["selected_model_family"])' "$BIRDCAST_UK_REANALYSIS_RUN_DIR/model-selection.json")"
lp_predictions="$BIRDCAST_UK_REANALYSIS_RUN_DIR/$selected/predictions_wide_lp.csv"
sp_predictions="$BIRDCAST_UK_REANALYSIS_RUN_DIR/$selected/predictions_wide_sp.csv"
test -s "$lp_predictions"
test -s "$sp_predictions"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli historical boundary \
  --output "$BIRDCAST_UK_ARTIFACT_ROOT/assets/uk-boundary.geojson"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis publish-wide \
  --lp-csv "$lp_predictions" \
  --sp-csv "$sp_predictions" \
  --comparison "$BIRDCAST_UK_REANALYSIS_RUN_DIR/model-selection.json" \
  --output-root "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --model-family "$selected"

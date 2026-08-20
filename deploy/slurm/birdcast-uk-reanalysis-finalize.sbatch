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

# Research benchmark finalizer only. Production promotion is performed by the
# component-selected publish job and never by this family-level comparison.

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_REANALYSIS_RUN_DIR:?Set the immutable model run directory}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis compare \
  --gamm-metrics "$BIRDCAST_UK_REANALYSIS_RUN_DIR/gamm/metrics.json" \
  --xgboost-metrics "$BIRDCAST_UK_REANALYSIS_RUN_DIR/xgboost/metrics.json" \
  --output "$BIRDCAST_UK_REANALYSIS_RUN_DIR/model-selection.json"

"$BIRDCAST_UK_PYTHON" -c \
  'import json,sys; result=json.load(open(sys.argv[1])); print("research_selected_model_family=" + result["selected_model_family"])' \
  "$BIRDCAST_UK_REANALYSIS_RUN_DIR/model-selection.json"

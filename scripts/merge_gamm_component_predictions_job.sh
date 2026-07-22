#!/bin/bash
set -euo pipefail

# Merge a complete daily prediction archive after the Slurm inference array.
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set the daily prediction directory}"
: "${BIRDCAST_UK_COMPONENT_MERGED_DIR:?Set the merged prediction directory}"
: "${BIRDCAST_UK_ROOT:?Set the uk-birdcast checkout root}"
: "${BIRDCAST_UK_EXPECTED_DAYS:=365}"

mkdir -p "$BIRDCAST_UK_COMPONENT_MERGED_DIR"
for pulse in lp sp; do
  python3 "$BIRDCAST_UK_ROOT/scripts/merge_gamm_component_predictions.py" \
    --input-dir "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR" \
    --pulse "$pulse" \
    --output "$BIRDCAST_UK_COMPONENT_MERGED_DIR/predictions_wide_${pulse}.csv" \
    --expected-days "$BIRDCAST_UK_EXPECTED_DAYS"
done

#!/bin/bash
set -euo pipefail

# Merge a complete daily prediction archive after the Slurm inference array.
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set the daily prediction directory}"
: "${BIRDCAST_UK_COMPONENT_MERGED_DIR:?Set the merged prediction directory}"
: "${BIRDCAST_UK_ROOT:?Set the uk-birdcast checkout root}"
: "${BIRDCAST_UK_EXPECTED_DAYS:=365}"
: "${BIRDCAST_UK_EXPECTED_START_DAY:?Set the first validated model day (YYYY-MM-DD)}"
: "${BIRDCAST_UK_EXPECTED_END_DAY:?Set the last validated model day (YYYY-MM-DD)}"
: "${BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256:?Set the reviewed manifest hash}"

mkdir -p "$BIRDCAST_UK_COMPONENT_MERGED_DIR"
for pulse in lp sp; do
  "$BIRDCAST_UK_PYTHON" "$BIRDCAST_UK_ROOT/scripts/merge_gamm_component_predictions.py" \
    --input-dir "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR" \
    --pulse "$pulse" \
    --output "$BIRDCAST_UK_COMPONENT_MERGED_DIR/predictions_wide_${pulse}.csv" \
    --expected-days "$BIRDCAST_UK_EXPECTED_DAYS" \
    --expected-start-day "$BIRDCAST_UK_EXPECTED_START_DAY" \
    --expected-end-day "$BIRDCAST_UK_EXPECTED_END_DAY" \
    --expected-component-manifest-sha256 "$BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256"
done
sidecar="$BIRDCAST_UK_COMPONENT_MERGED_DIR/component-manifest.sha256"
temporary_sidecar="$(mktemp "$BIRDCAST_UK_COMPONENT_MERGED_DIR/.component-manifest.XXXXXX")"
trap 'rm -f "$temporary_sidecar"' EXIT
printf '%s\n' "$BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256" > "$temporary_sidecar"
mv "$temporary_sidecar" "$sidecar"
trap - EXIT

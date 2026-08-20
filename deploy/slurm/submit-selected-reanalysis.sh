#!/bin/bash
# Submit the immutable, component-selected UK GAMM release. Model files and
# merged predictions remain under private roots; only manifest-referenced
# browser assets are handed to the product-scoped publisher.
set -Eeuo pipefail

: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the full reviewed main commit SHA}"
: "${BIRDCAST_UK_COMPONENT_MANIFEST:?Set the validated component manifest}"
: "${BIRDCAST_UK_COMPONENT_AUTHORITY_DIR:?Set a private immutable model-authority directory}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the validated daily ERA5 grid directory}"
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set a private prediction directory}"
: "${BIRDCAST_UK_COMPONENT_MERGED_DIR:?Set a private merge directory}"
: "${BIRDCAST_UK_EXPECTED_DAYS:=365}"
: "${BIRDCAST_UK_EXPECTED_START_DAY:?Set the validated first model day}"
: "${BIRDCAST_UK_EXPECTED_END_DAY:?Set the validated last model day}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_EXPECTED_SELECTION_ID:=uk-gamm-heldout-v2-sp-vector-925}"

export PYTHONPATH="$BIRDCAST_UK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
contract="$("$BIRDCAST_UK_PYTHON" - "$BIRDCAST_UK_COMPONENT_MANIFEST" <<'PY'
from pathlib import Path
import sys
from birdcast_uk.selected_model import (
    COMPONENT_MANIFEST_SHA256,
    QUALIFIED_DAY_COUNT,
    QUALIFIED_FIRST_DAY,
    QUALIFIED_LAST_DAY,
    SELECTION_ID,
    validate_component_manifest,
)

validate_component_manifest(Path(sys.argv[1]), verify_model_files=True)
print(
    SELECTION_ID,
    COMPONENT_MANIFEST_SHA256,
    QUALIFIED_FIRST_DAY.isoformat(),
    QUALIFIED_LAST_DAY.isoformat(),
    QUALIFIED_DAY_COUNT,
)
PY
)"
read -r actual_selection_id BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256 \
  qualified_start qualified_end qualified_days <<< "$contract"
test "$actual_selection_id" = "$BIRDCAST_UK_EXPECTED_SELECTION_ID"
test "$BIRDCAST_UK_EXPECTED_START_DAY" = "$qualified_start"
test "$BIRDCAST_UK_EXPECTED_END_DAY" = "$qualified_end"
test "$BIRDCAST_UK_EXPECTED_DAYS" -eq "$qualified_days"
export BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256

authority_dir="$BIRDCAST_UK_COMPONENT_AUTHORITY_DIR/$BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256"
authority_manifest="$authority_dir/component-selection.json"
mkdir -p "$authority_dir"
test ! -L "$authority_dir"
if [ -e "$authority_manifest" ]; then
  test ! -L "$authority_manifest"
  cmp "$BIRDCAST_UK_COMPONENT_MANIFEST" "$authority_manifest"
else
  temporary_manifest="$(mktemp "$authority_dir/.component-selection.XXXXXX")"
  trap 'rm -f "$temporary_manifest"' EXIT
  cp "$BIRDCAST_UK_COMPONENT_MANIFEST" "$temporary_manifest"
  mv "$temporary_manifest" "$authority_manifest"
  trap - EXIT
fi
BIRDCAST_UK_COMPONENT_MANIFEST="$authority_manifest"
export BIRDCAST_UK_COMPONENT_MANIFEST

"$BIRDCAST_UK_PYTHON" - "$BIRDCAST_UK_ERA5_GRID_DIR" <<'PY'
from pathlib import Path
import sys
from birdcast_uk.selected_model import validate_grid_archive

paths = validate_grid_archive(Path(sys.argv[1]))
print(f"validated {len(paths)} selected-model ERA5 grid days")
PY

BIRDCAST_UK_PUBLICATION_PRODUCTS=gam-era5

while IFS= read -r variable_name; do
  export "$variable_name"
done < <(compgen -A variable BIRDCAST_UK_)

cd "$BIRDCAST_UK_ROOT"
mkdir -p logs "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR" "$BIRDCAST_UK_COMPONENT_MERGED_DIR"
for private_output in "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR" "$BIRDCAST_UK_COMPONENT_MERGED_DIR"; do
  if [ -n "$(find "$private_output" -mindepth 1 -maxdepth 1 -print -quit)" ]; then
    echo "Selected-component output directory must start empty: $private_output" >&2
    exit 1
  fi
done

submitted=()
cancel_partial_chain() {
  status=$?
  trap - ERR
  if [ "$status" -ne 0 ] && [ "${#submitted[@]}" -gt 0 ]; then
    scancel "${submitted[@]}" || true
  fi
  exit "$status"
}
trap cancel_partial_chain ERR

prediction="$(sbatch --parsable --array="0-$((BIRDCAST_UK_EXPECTED_DAYS - 1))" deploy/slurm/birdcast-uk-component-predict.sbatch)"
prediction="${prediction%%;*}"
submitted+=("$prediction")
merged="$(sbatch --parsable --dependency="afterok:${prediction}" deploy/slurm/birdcast-uk-component-merge.sbatch)"
merged="${merged%%;*}"
submitted+=("$merged")
modelled="$(sbatch --parsable --dependency="afterok:${merged}" deploy/slurm/birdcast-uk-component-publish.sbatch)"
modelled="${modelled%%;*}"
submitted+=("$modelled")
published="$(sbatch --parsable --dependency="afterok:${modelled}" deploy/slurm/birdcast-uk-object-store-publish.sbatch)"
published="${published%%;*}"
submitted+=("$published")
trap - ERR

printf 'prediction=%s\nmerged=%s\nmodelled=%s\npublished=%s\n' \
  "$prediction" "$merged" "$modelled" "$published"

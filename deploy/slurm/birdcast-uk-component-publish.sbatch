#!/bin/bash
#SBATCH --job-name=birdcast-uk-component-publish
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=logs/birdcast-uk-component-publish-%j.out
#SBATCH --error=logs/birdcast-uk-component-publish-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_ROOT:?Set the immutable repository release}"
: "${BIRDCAST_UK_RELEASE_SHA:?Set the reviewed release SHA}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_ARTIFACT_ROOT:?Set the compact public artifact root}"
: "${BIRDCAST_UK_COMPONENT_MERGED_DIR:?Set the validated private merge directory}"
: "${BIRDCAST_UK_COMPONENT_MANIFEST:?Set the hash-bearing component manifest}"
: "${BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256:?Set the reviewed manifest hash}"
: "${BIRDCAST_UK_COMPONENT_PUBLICATION:=$BIRDCAST_UK_ROOT/configs/gamm_uk_holdout_component_publication.json}"

module load jaspy/3.12/v20250704
export PYTHONPATH="$BIRDCAST_UK_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_UK_ROOT/deploy/scripts/verify-immutable-release.sh"
"$BIRDCAST_UK_PYTHON" - "$BIRDCAST_UK_COMPONENT_MANIFEST" <<'PY'
from pathlib import Path
import sys
from birdcast_uk.selected_model import validate_component_manifest

validate_component_manifest(Path(sys.argv[1]), verify_model_files=True)
PY
test "$(cat "$BIRDCAST_UK_COMPONENT_MERGED_DIR/component-manifest.sha256")" = \
  "$BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli historical boundary \
  --output "$BIRDCAST_UK_ARTIFACT_ROOT/assets/uk-boundary.geojson"
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli reanalysis publish-wide \
  --lp-csv "$BIRDCAST_UK_COMPONENT_MERGED_DIR/predictions_wide_lp.csv" \
  --sp-csv "$BIRDCAST_UK_COMPONENT_MERGED_DIR/predictions_wide_sp.csv" \
  --comparison "$BIRDCAST_UK_COMPONENT_PUBLICATION" \
  --component-manifest "$BIRDCAST_UK_COMPONENT_MANIFEST" \
  --output-root "$BIRDCAST_UK_ARTIFACT_ROOT" \
  --model-family gamm

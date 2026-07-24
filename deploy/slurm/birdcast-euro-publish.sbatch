#!/bin/bash
# Publish only a fully reconciled, externally validated Europe reanalysis.
#SBATCH --job-name=birdcast-euro-publish
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=16G
#SBATCH --output=logs/birdcast-euro-publish-%j.out
#SBATCH --error=logs/birdcast-euro-publish-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
: "${BIRDCAST_EURO_PREDICTIONS:?Set the passed model predictions CSV}"
: "${BIRDCAST_EURO_ARTIFACT_ROOT:?Set the staged Europe public artifact root}"
: "${BIRDCAST_EURO_MODEL_ID:?Set the Europe model ID}"
: "${BIRDCAST_EURO_ALOFT_RADAR_COUNT:?Set the Aloft training radar count}"
: "${BIRDCAST_EURO_UK_SP_RADAR_COUNT:?Set the UK SP radar count}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata path}"
: "${BIRDCAST_EURO_SOURCE_FIDELITY:?Set the passed source fidelity summary}"
: "${BIRDCAST_EURO_ERA5_FIDELITY:?Set the passed ERA5 fidelity summary}"
: "${BIRDCAST_EURO_TRAINING_FIDELITY:?Set the passed training fidelity report}"
: "${BIRDCAST_EURO_MODEL_VALIDATION:?Set the passed GAMM validation report}"
: "${BIRDCAST_EURO_PUBLICATION_FIDELITY:?Set publication fidelity report output}"
: "${BIRDCAST_EURO_OBJECT_STORE_BUCKET:?Set the public Object Store bucket}"
: "${BIRDCAST_EURO_OBJECT_STORE_ENDPOINT:?Set the public Object Store endpoint}"
: "${BIRDCAST_EURO_AWS_PROFILE:?Set the Object Store AWS profile}"
: "${BIRDCAST_EURO_OBJECT_PREFIX:?Set the public Object Store prefix}"

module load jaspy/3.12/v20250704
mkdir -p "$BIRDCAST_EURO_ARTIFACT_ROOT/validation"
cp "$BIRDCAST_EURO_SOURCE_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/source-fidelity.json"
cp "$BIRDCAST_EURO_ERA5_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/era5-fidelity.json"
cp "$BIRDCAST_EURO_TRAINING_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/training-fidelity.json"
cp "$BIRDCAST_EURO_MODEL_VALIDATION" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/model-validation.json"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli europe publish \
  --predictions "$BIRDCAST_EURO_PREDICTIONS" --output-root "$BIRDCAST_EURO_ARTIFACT_ROOT" \
  --model-id "$BIRDCAST_EURO_MODEL_ID" --aloft-radar-count "$BIRDCAST_EURO_ALOFT_RADAR_COUNT" \
  --uk-sp-radar-count "$BIRDCAST_EURO_UK_SP_RADAR_COUNT" --validation-url "validation/model-validation.json" \
  --radars "$BIRDCAST_EURO_RADARS" --release-status published
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/validate_europe_publication.py" \
  --predictions "$BIRDCAST_EURO_PREDICTIONS" --output-root "$BIRDCAST_EURO_ARTIFACT_ROOT" \
  --source-fidelity "$BIRDCAST_EURO_SOURCE_FIDELITY" --training-fidelity "$BIRDCAST_EURO_TRAINING_FIDELITY" \
  --model-validation "$BIRDCAST_EURO_MODEL_VALIDATION" --output "$BIRDCAST_EURO_PUBLICATION_FIDELITY"

# A public manifest is the promotion switch. All assets reach the Object Store
# before it does, so a web host can never activate a partial release.
target="s3://${BIRDCAST_EURO_OBJECT_STORE_BUCKET}/${BIRDCAST_EURO_OBJECT_PREFIX}"
aws --profile "$BIRDCAST_EURO_AWS_PROFILE" --endpoint-url "$BIRDCAST_EURO_OBJECT_STORE_ENDPOINT" s3 sync \
  "$BIRDCAST_EURO_ARTIFACT_ROOT" "$target" --acl public-read --only-show-errors \
  --exclude "latest/reanalysis.json"
aws --profile "$BIRDCAST_EURO_AWS_PROFILE" --endpoint-url "$BIRDCAST_EURO_OBJECT_STORE_ENDPOINT" s3 cp \
  "$BIRDCAST_EURO_ARTIFACT_ROOT/latest/reanalysis.json" "$target/latest/reanalysis.json" \
  --acl public-read --only-show-errors

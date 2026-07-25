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
: "${BIRDCAST_EURO_PREDICTIONS_ROOT:?Set the passed daily prediction partition root}"
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
: "${BIRDCAST_EURO_PUBLIC_HOST:?Set the public host address}"
: "${BIRDCAST_EURO_PUBLIC_USER:?Set the public host SSH user}"
: "${BIRDCAST_EURO_PUBLIC_STAGE_ROOT:?Set the public host release-stage root}"

module load jaspy/3.12/v20250704
mkdir -p "$BIRDCAST_EURO_ARTIFACT_ROOT/validation"
cp "$BIRDCAST_EURO_SOURCE_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/source-fidelity.json"
cp "$BIRDCAST_EURO_ERA5_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/era5-fidelity.json"
cp "$BIRDCAST_EURO_TRAINING_FIDELITY" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/training-fidelity.json"
cp "$BIRDCAST_EURO_MODEL_VALIDATION" "$BIRDCAST_EURO_ARTIFACT_ROOT/validation/model-validation.json"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli europe publish \
  --predictions-root "$BIRDCAST_EURO_PREDICTIONS_ROOT" --output-root "$BIRDCAST_EURO_ARTIFACT_ROOT" \
  --model-id "$BIRDCAST_EURO_MODEL_ID" --aloft-radar-count "$BIRDCAST_EURO_ALOFT_RADAR_COUNT" \
  --uk-sp-radar-count "$BIRDCAST_EURO_UK_SP_RADAR_COUNT" --validation-url "validation/model-validation.json" \
  --radars "$BIRDCAST_EURO_RADARS" --release-status published
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/validate_europe_publication.py" \
  --predictions-root "$BIRDCAST_EURO_PREDICTIONS_ROOT" --output-root "$BIRDCAST_EURO_ARTIFACT_ROOT" \
  --source-fidelity "$BIRDCAST_EURO_SOURCE_FIDELITY" --era5-fidelity "$BIRDCAST_EURO_ERA5_FIDELITY" \
  --training-fidelity "$BIRDCAST_EURO_TRAINING_FIDELITY" \
  --model-validation "$BIRDCAST_EURO_MODEL_VALIDATION" --output "$BIRDCAST_EURO_PUBLICATION_FIDELITY"

# Transfer only a release that has passed every preceding gate. The host-side
# activator independently checks the manifest and switches its Nginx symlink.
remote="${BIRDCAST_EURO_PUBLIC_USER}@${BIRDCAST_EURO_PUBLIC_HOST}"
remote_stage="/tmp/${BIRDCAST_EURO_MODEL_ID}-${SLURM_JOB_ID}"
rsync -a --delete -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=yes" \
  "$BIRDCAST_EURO_ARTIFACT_ROOT/" "$remote:$remote_stage/"
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes "$remote" \
  "sudo -n -u birdcast /bin/sh /opt/birdcast-uk/repo/deploy/scripts/birdcast-euro-activate.sh '$remote_stage' '$BIRDCAST_EURO_PUBLIC_STAGE_ROOT' /opt/birdcast-euro/artifacts-current"

#!/bin/bash
# Build only the compact relative coastal activity/flow publication assets.
# This reads the frozen derived cohort table and never writes raw radar products.
#SBATCH --job-name=birdcast-coastal-relative
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-coastal-relative-%j.out
#SBATCH --error=logs/birdcast-coastal-relative-%j.err

set -e
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable BirdCast Europe checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_COASTAL_TRAINING_CSV:?Set the frozen coastal derived table}"
: "${BIRDCAST_COASTAL_COHORT:?Set the frozen coastal cohort manifest}"
: "${BIRDCAST_COASTAL_ARTIFACT_ROOT:?Set the coastal publication artifact root}"
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli coastal build-relative-flow \
  --training-csv "$BIRDCAST_COASTAL_TRAINING_CSV" \
  --cohort "$BIRDCAST_COASTAL_COHORT" \
  --output-root "$BIRDCAST_COASTAL_ARTIFACT_ROOT"

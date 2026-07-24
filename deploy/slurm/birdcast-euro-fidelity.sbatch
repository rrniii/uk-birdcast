#!/bin/bash
# Submit after every streaming chunk has completed, with the identical array
# range and a modest concurrency limit. Each task re-streams one radar-month;
# it writes only a compact pass/fail audit and never raw VPTS profiles.
#SBATCH --job-name=birdcast-euro-fidelity
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=6G
#SBATCH --output=logs/birdcast-euro-fidelity-%A_%a.out
#SBATCH --error=logs/birdcast-euro-fidelity-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_CHUNK_MANIFEST:?Set the radar-month JSONL manifest}"
: "${BIRDCAST_EURO_HOURLY_ROOT:?Set the derived hourly output root}"
: "${BIRDCAST_EURO_FIDELITY_ROOT:?Set the compact fidelity-report root}"
: "${SLURM_ARRAY_TASK_ID:?Submit this job as an array}"

module load jaspy/3.12/v20250704
mkdir -p "$BIRDCAST_EURO_FIDELITY_ROOT"
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli europe verify-chunk \
  --manifest "$BIRDCAST_EURO_CHUNK_MANIFEST" \
  --index "$SLURM_ARRAY_TASK_ID" \
  --hourly-root "$BIRDCAST_EURO_HOURLY_ROOT" \
  --output "$BIRDCAST_EURO_FIDELITY_ROOT/${SLURM_ARRAY_TASK_ID}.json"

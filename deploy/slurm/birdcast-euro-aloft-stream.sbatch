#!/bin/bash
# Submit with --array=0-<chunk_count-1>%200 after `birdcast-uk europe chunk-manifest`.
# Each task streams one radar-month and persists derived hourly Parquet only.
#SBATCH --job-name=birdcast-euro-aloft
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=02:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=6G
#SBATCH --output=logs/birdcast-euro-aloft-%A_%a.out
#SBATCH --error=logs/birdcast-euro-aloft-%A_%a.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_CHUNK_MANIFEST:?Set the radar-month JSONL manifest}"
: "${BIRDCAST_EURO_HOURLY_ROOT:?Set the derived hourly output root}"
: "${SLURM_ARRAY_TASK_ID:?Submit this job as an array}"

module load jaspy/3.12/v20250704
"$BIRDCAST_EURO_PYTHON" -m birdcast_uk.cli europe stream-chunk \
  --manifest "$BIRDCAST_EURO_CHUNK_MANIFEST" \
  --index "$SLURM_ARRAY_TASK_ID" \
  --output-root "$BIRDCAST_EURO_HOURLY_ROOT"

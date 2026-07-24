#!/bin/bash
# Run only after the full re-stream fidelity array completes successfully.
#SBATCH --job-name=birdcast-euro-fidelity-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:15:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --output=logs/birdcast-euro-fidelity-merge-%j.out
#SBATCH --error=logs/birdcast-euro-fidelity-merge-%j.err

set -euo pipefail
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
: "${BIRDCAST_EURO_CHUNK_MANIFEST:?Set the radar-month JSONL manifest}"
: "${BIRDCAST_EURO_FIDELITY_ROOT:?Set the compact fidelity-report root}"
: "${BIRDCAST_EURO_FIDELITY_SUMMARY:?Set the output summary path}"

"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/merge_europe_fidelity.py" \
  --manifest "$BIRDCAST_EURO_CHUNK_MANIFEST" \
  --reports-root "$BIRDCAST_EURO_FIDELITY_ROOT" \
  --output "$BIRDCAST_EURO_FIDELITY_SUMMARY"

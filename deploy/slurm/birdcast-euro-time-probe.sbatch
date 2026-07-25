#!/bin/bash
# Evaluate one candidate continuous UTC smooth on held-out Aloft sites.
#SBATCH --job-name=birdcast-euro-time-probe
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=64G
#SBATCH --output=logs/birdcast-euro-time-probe-%j.out
#SBATCH --error=logs/birdcast-euro-time-probe-%j.err

set -euo pipefail
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_RSCRIPT:?Set the pinned JASMIN Rscript executable}"
: "${BIRDCAST_EURO_MODEL_SPEC:?Set the passed Europe model specification}"
: "${BIRDCAST_EURO_TIME_K:?Set candidate continuous UTC smooth dimension}"
: "${BIRDCAST_EURO_PROBE_OUTPUT:?Set probe output JSON}"
: "${BIRDCAST_EURO_PROBE_TARGET:?Set target response}"
mkdir -p "$(dirname "$BIRDCAST_EURO_PROBE_OUTPUT")"
"$BIRDCAST_EURO_RSCRIPT" "$BIRDCAST_EURO_ROOT/scripts/probe_europe_time_resolution.R" \
  "$BIRDCAST_EURO_MODEL_SPEC" "$BIRDCAST_EURO_TIME_K" \
  "$BIRDCAST_EURO_PROBE_OUTPUT" "$BIRDCAST_EURO_PROBE_TARGET"

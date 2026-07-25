#!/bin/bash
# Fit only after the streaming, ERA5 and training-table reconciliation gates pass.
#SBATCH --job-name=birdcast-euro-gamm
#SBATCH --account=ncas_radar
#SBATCH --qos=long
#SBATCH --partition=standard
#SBATCH --time=48:00:00
# The ncas_radar long QoS currently permits one CPU per job and up to 128 GB.
# mgcv uses a single R process here; retaining the complete fold set is more
# important than requesting unschedulable parallel CPUs.
#SBATCH --cpus-per-task=1
#SBATCH --mem=128G
#SBATCH --output=logs/birdcast-euro-gamm-%j.out
#SBATCH --error=logs/birdcast-euro-gamm-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_EURO_ROOT:?Set the birdcast_euro checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_RSCRIPT:?Set the pinned JASMIN Rscript executable}"
: "${BIRDCAST_EURO_MODEL_SPEC:?Set the frozen Europe model spec}"
: "${BIRDCAST_EURO_RUN_DIR:?Set an immutable run directory}"

mkdir -p "$BIRDCAST_EURO_RUN_DIR"
fit_args=(
  "$BIRDCAST_EURO_MODEL_SPEC"
  "$BIRDCAST_EURO_RUN_DIR"
)
if [ -n "${BIRDCAST_EURO_GRID_TABLE:-}" ]; then
  fit_args+=("$BIRDCAST_EURO_GRID_TABLE")
fi
"$BIRDCAST_EURO_RSCRIPT" "$BIRDCAST_EURO_ROOT/scripts/fit_europe_gamm.R" "${fit_args[@]}"

"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/validate_europe_gamm.py" \
  --metrics "$BIRDCAST_EURO_RUN_DIR/metrics.json" \
  --output "$BIRDCAST_EURO_RUN_DIR/validation.json" \
  --require-pass

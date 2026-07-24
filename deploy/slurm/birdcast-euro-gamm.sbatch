#!/bin/bash
# Fit only after the streaming, ERA5 and training-table reconciliation gates pass.
#SBATCH --job-name=birdcast-euro-gamm
#SBATCH --account=ncas_radar
#SBATCH --qos=long
#SBATCH --partition=standard
#SBATCH --time=48:00:00
#SBATCH --cpus-per-task=8
#SBATCH --mem=192G
#SBATCH --output=logs/birdcast-euro-gamm-%j.out
#SBATCH --error=logs/birdcast-euro-gamm-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_EURO_ROOT:?Set the birdcast_euro checkout}"
: "${BIRDCAST_EURO_MODEL_SPEC:?Set the frozen Europe model spec}"
: "${BIRDCAST_EURO_GRID_TABLE:?Set the European ERA5 prediction grid CSV}"
: "${BIRDCAST_EURO_RUN_DIR:?Set an immutable run directory}"

mkdir -p "$BIRDCAST_EURO_RUN_DIR"
module load jasr/4.4/v20250704
Rscript "$BIRDCAST_EURO_ROOT/scripts/fit_europe_gamm.R" \
  "$BIRDCAST_EURO_MODEL_SPEC" \
  "$BIRDCAST_EURO_RUN_DIR" \
  "$BIRDCAST_EURO_GRID_TABLE"

python3 "$BIRDCAST_EURO_ROOT/scripts/validate_europe_gamm.py" \
  --metrics "$BIRDCAST_EURO_RUN_DIR/metrics.json" \
  --output "$BIRDCAST_EURO_RUN_DIR/validation.json" \
  --require-pass

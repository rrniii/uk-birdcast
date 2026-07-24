#!/bin/bash
# Fail closed unless every model-year Europe grid day is present.
#SBATCH --job-name=birdcast-euro-grid-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=04:00:00
#SBATCH --cpus-per-task=2
#SBATCH --mem=32G
#SBATCH --output=logs/birdcast-euro-grid-merge-%j.out
#SBATCH --error=logs/birdcast-euro-grid-merge-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_GRID_DAILY_ROOT:?Set the Europe daily grid root}"
: "${BIRDCAST_EURO_GRID_TABLE:?Set the Europe merged grid CSV output}"

module load jaspy/3.12/v20250704
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/merge_europe_grid.py" \
  --grid-root "$BIRDCAST_EURO_GRID_DAILY_ROOT" --output "$BIRDCAST_EURO_GRID_TABLE" \
  --start-day 2025-07-14 --end-day 2026-07-13

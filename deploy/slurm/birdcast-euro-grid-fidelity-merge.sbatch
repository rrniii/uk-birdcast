#!/bin/bash
# Merge all independent Europe grid fidelity reports.
#SBATCH --job-name=birdcast-euro-grid-fidelity-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=4G
#SBATCH --output=logs/birdcast-euro-grid-fidelity-merge-%j.out
#SBATCH --error=logs/birdcast-euro-grid-fidelity-merge-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u
: "${BIRDCAST_EURO_ROOT:?Set immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set branch Python executable}"
: "${BIRDCAST_EURO_RELEASE:?Set Europe release identifier}"
: "${BIRDCAST_EURO_GRID_FIDELITY_ROOT:?Set grid fidelity root}"
: "${BIRDCAST_EURO_GRID_FIDELITY_SUMMARY:?Set grid fidelity summary output}"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/merge_europe_grid_fidelity.py" \
  --root "$BIRDCAST_EURO_GRID_FIDELITY_ROOT" --output "$BIRDCAST_EURO_GRID_FIDELITY_SUMMARY" \
  --start-day 2025-07-14 --end-day 2026-07-13 --release-id "$BIRDCAST_EURO_RELEASE"

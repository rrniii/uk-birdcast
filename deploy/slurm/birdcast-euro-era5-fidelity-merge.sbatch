#!/bin/bash
# Fail closed unless all Europe ERA5 daily reconstructions pass.
#SBATCH --job-name=birdcast-euro-era5-fidelity-merge
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:15:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=2G
#SBATCH --output=logs/birdcast-euro-era5-fidelity-merge-%j.out
#SBATCH --error=logs/birdcast-euro-era5-fidelity-merge-%j.err

set -euo pipefail
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_RELEASE:?Set the immutable Europe release identifier}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
export PYTHONPATH="$BIRDCAST_EURO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata}"
: "${BIRDCAST_EURO_ERA5_FIDELITY_ROOT:?Set the Europe ERA5 audit root}"
: "${BIRDCAST_EURO_ERA5_FIDELITY_SUMMARY:?Set the Europe ERA5 audit summary output}"
radars_sha256="$(sha256sum "$BIRDCAST_EURO_RADARS" | awk '{print $1}')"
"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/merge_europe_era5_fidelity.py" \
  --reports-root "$BIRDCAST_EURO_ERA5_FIDELITY_ROOT" --output "$BIRDCAST_EURO_ERA5_FIDELITY_SUMMARY" \
  --start-day 2025-07-14 --end-day 2026-07-13 --release-id "$BIRDCAST_EURO_RELEASE" \
  --radars-sha256 "$radars_sha256"

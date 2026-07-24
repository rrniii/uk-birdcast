#!/bin/bash
# Build radar metadata only after every source reconstruction check passed.
#SBATCH --job-name=birdcast-euro-metadata
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=00:30:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=8G
#SBATCH --output=logs/birdcast-euro-metadata-%j.out
#SBATCH --error=logs/birdcast-euro-metadata-%j.err

set -euo pipefail
: "${BIRDCAST_EURO_ROOT:?Set the immutable Europe release checkout}"
: "${BIRDCAST_EURO_PYTHON:?Set the branch Python executable}"
: "${BIRDCAST_EURO_HOURLY_ROOT:?Set the derived hourly output root}"
: "${BIRDCAST_EURO_UK_RADARS:?Set the read-only UK radar metadata}"
: "${BIRDCAST_EURO_RADARS:?Set the Europe radar metadata output}"

"$BIRDCAST_EURO_PYTHON" "$BIRDCAST_EURO_ROOT/scripts/build_europe_radar_metadata.py" \
  --aloft-parquet "$BIRDCAST_EURO_HOURLY_ROOT/**/*.parquet" \
  --uk-radars "$BIRDCAST_EURO_UK_RADARS" \
  --overrides "$BIRDCAST_EURO_ROOT/configs/europe_radar_overrides.json" \
  --output "$BIRDCAST_EURO_RADARS"

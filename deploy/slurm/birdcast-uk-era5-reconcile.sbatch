#!/bin/bash
#SBATCH --job-name=birdcast-uk-era5-reconcile
#SBATCH --account=ncas_radar
#SBATCH --qos=standard
#SBATCH --partition=standard
#SBATCH --time=24:00:00
#SBATCH --cpus-per-task=1
#SBATCH --mem=12G
#SBATCH --output=logs/birdcast-uk-era5-reconcile-%j.out
#SBATCH --error=logs/birdcast-uk-era5-reconcile-%j.err

set -eo pipefail
. /etc/profile.d/modules.sh
. /etc/profile.d/zz-modules.sh
set -u

: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"
: "${BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY:?Set the historical inventory path}"
: "${BIRDCAST_UK_ERA5_RAW_DIR:?Set the ERA5 raw directory}"
: "${BIRDCAST_UK_ERA5_FEATURE_DIR:?Set the ERA5 site-feature directory}"
: "${BIRDCAST_UK_RADARS_FILE:?Set radar metadata}"

module load jaspy/3.12/v20250704
"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 readiness
end_date="$("$BIRDCAST_UK_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["window"]["end_date"])' "$BIRDCAST_UK_VPTS_ARCHIVE_INVENTORY")"

repaired=0
for offset in $(seq 0 364); do
  day="$(date -u -d "${end_date} -${offset} days" +%Y-%m-%d)"
  stamp="${day//-/}"
  single="$BIRDCAST_UK_ERA5_RAW_DIR/era5_single_levels_${stamp}_uk.nc"
  pressure="$BIRDCAST_UK_ERA5_RAW_DIR/era5_pressure_levels_${stamp}_uk.nc"
  features="$BIRDCAST_UK_ERA5_FEATURE_DIR/era5_site_features_${stamp}.json"
  if "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 validate-day \
    --day "$day" \
    --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
    --feature-output "$features" >/dev/null; then
    continue
  fi
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 build-day \
    --day "$day" \
    --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
    --feature-output "$features" \
    --radars "$BIRDCAST_UK_RADARS_FILE" \
    --download \
    --overwrite
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 validate-day \
    --day "$day" \
    --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
    --feature-output "$features"
  repaired=$((repaired + 1))
done

for offset in $(seq 0 364); do
  day="$(date -u -d "${end_date} -${offset} days" +%Y-%m-%d)"
  stamp="${day//-/}"
  "$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 validate-day \
    --day "$day" \
    --raw-dir "$BIRDCAST_UK_ERA5_RAW_DIR" \
    --feature-output "$BIRDCAST_UK_ERA5_FEATURE_DIR/era5_site_features_${stamp}.json" \
    >/dev/null
done
printf 'ERA5 reconciliation complete: repaired=%s window_days=365\n' "$repaired"

#!/bin/bash
# Submit the independent historical pipeline. Run from the deployed repo after
# sourcing the private JASMIN environment file.
set -euo pipefail

: "${BIRDCAST_UK_ROOT:?Set the standalone repository path}"
: "${BIRDCAST_UK_PYTHON:?Set the project Python executable}"

# Environment files commonly assign without `export`. Slurm receives only
# exported values, so promote the complete BirdCast namespace before sbatch.
while IFS= read -r variable_name; do
  export "$variable_name"
done < <(compgen -A variable BIRDCAST_UK_)

cd "$BIRDCAST_UK_ROOT"

"$BIRDCAST_UK_PYTHON" -m birdcast_uk.cli era5 readiness

inventory="$(sbatch --parsable deploy/slurm/birdcast-uk-vpts-historical-inventory.sbatch)"
hourly="$(sbatch --parsable --dependency="afterok:${inventory}" deploy/slurm/birdcast-uk-vpts-hourly.sbatch)"
era5="$(sbatch --parsable --dependency="afterok:${inventory}" deploy/slurm/birdcast-uk-era5-period-backfill.sbatch)"
era5_reconciled="$(sbatch --parsable --dependency="afterany:${era5}" deploy/slurm/birdcast-uk-era5-reconcile.sbatch)"
joined="$(sbatch --parsable --dependency="afterok:${hourly}:${era5_reconciled}" deploy/slurm/birdcast-uk-feature-join.sbatch)"
prepared="$(sbatch --parsable --dependency="afterok:${joined}" deploy/slurm/birdcast-uk-reanalysis-prepare.sbatch)"
grid="$(sbatch --parsable --dependency="afterok:${prepared}" deploy/slurm/birdcast-uk-era5-grid-day.sbatch)"
grid_reconciled="$(sbatch --parsable --dependency="afterany:${grid}" deploy/slurm/birdcast-uk-era5-grid-reconcile.sbatch)"
merged="$(sbatch --parsable --dependency="afterok:${grid_reconciled}" deploy/slurm/birdcast-uk-era5-grid-merge.sbatch)"
export BIRDCAST_UK_REANALYSIS_RUN_DIR="${BIRDCAST_UK_ARTIFACT_ROOT}/reanalysis/run-$(date -u +%Y%m%dT%H%M%SZ)"
gamm="$(sbatch --parsable --dependency="afterok:${merged}" deploy/slurm/birdcast-uk-reanalysis-gamm.sbatch)"
xgboost="$(sbatch --parsable --dependency="afterok:${merged}" deploy/slurm/birdcast-uk-reanalysis-xgboost.sbatch)"
model="$(sbatch --parsable --dependency="afterok:${gamm}:${xgboost}" deploy/slurm/birdcast-uk-reanalysis-finalize.sbatch)"
published="$(sbatch --parsable --dependency="afterok:${model}" deploy/slurm/birdcast-uk-object-store-publish.sbatch)"
printf 'inventory=%s\nhourly=%s\nera5=%s\nera5_reconciled=%s\njoined=%s\nprepared=%s\ngrid=%s\ngrid_reconciled=%s\nmerged=%s\ngamm=%s\nxgboost=%s\nmodel=%s\npublished=%s\n' \
  "$inventory" "$hourly" "$era5" "$era5_reconciled" "$joined" "$prepared" "$grid" "$grid_reconciled" "$merged" "$gamm" "$xgboost" "$model" "$published"

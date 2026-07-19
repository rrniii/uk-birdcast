#!/bin/bash
# Submit the independent historical pipeline. Run from the deployed repo after
# sourcing the private JASMIN environment file.
set -euo pipefail

: "${BIRDCAST_UK_ROOT:?Set the standalone repository path}"
cd "$BIRDCAST_UK_ROOT"

inventory="$(sbatch --parsable deploy/slurm/birdcast-uk-vpts-historical-inventory.sbatch)"
hourly="$(sbatch --parsable --dependency="afterok:${inventory}" deploy/slurm/birdcast-uk-vpts-hourly.sbatch)"
era5="$(sbatch --parsable --dependency="afterok:${inventory}" deploy/slurm/birdcast-uk-era5-backfill.sbatch)"
joined="$(sbatch --parsable --dependency="afterok:${hourly}:${era5}" deploy/slurm/birdcast-uk-feature-join.sbatch)"
prepared="$(sbatch --parsable --dependency="afterok:${joined}" deploy/slurm/birdcast-uk-reanalysis-prepare.sbatch)"
grid="$(sbatch --parsable --dependency="afterok:${prepared}" deploy/slurm/birdcast-uk-era5-grid-day.sbatch)"
merged="$(sbatch --parsable --dependency="afterok:${grid}" deploy/slurm/birdcast-uk-era5-grid-merge.sbatch)"
model="$(sbatch --parsable --dependency="afterok:${merged}" deploy/slurm/birdcast-uk-reanalysis.sbatch)"
printf 'inventory=%s\nhourly=%s\nera5=%s\njoined=%s\nprepared=%s\ngrid=%s\nmerged=%s\nmodel=%s\n' \
  "$inventory" "$hourly" "$era5" "$joined" "$prepared" "$grid" "$merged" "$model"

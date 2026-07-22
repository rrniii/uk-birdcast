#!/bin/bash
set -euo pipefail

# Apply the selected GAMM components to one daily ERA5 grid built by the
# vertical-grid Slurm array. The array index deliberately follows the grid
# archive ordering so each result has an unambiguous UTC-date directory.
: "${BIRDCAST_UK_COMPONENT_MANIFEST:?Set the component selection manifest}"
: "${BIRDCAST_UK_ERA5_GRID_DIR:?Set the daily ERA5 grid directory}"
: "${BIRDCAST_UK_COMPONENT_PREDICTION_DIR:?Set the daily prediction output directory}"
: "${BIRDCAST_UK_ROOT:?Set the uk-birdcast checkout root}"
: "${SLURM_ARRAY_TASK_ID:?Run through a Slurm array}"

mapfile -t grid_files < <(
  find "$BIRDCAST_UK_ERA5_GRID_DIR" -maxdepth 1 -type f -name 'era5_grid_*.csv' -print | sort
)
grid="${grid_files[$SLURM_ARRAY_TASK_ID]:?array index is outside the ERA5 grid archive}"
stamp="${grid##*/era5_grid_}"
stamp="${stamp%.csv}"

Rscript "$BIRDCAST_UK_ROOT/scripts/predict_gamm_components.R" \
  "$BIRDCAST_UK_COMPONENT_MANIFEST" \
  "$grid" \
  "$BIRDCAST_UK_COMPONENT_PREDICTION_DIR/$stamp"

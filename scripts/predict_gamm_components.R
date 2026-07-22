#!/usr/bin/env Rscript

# Apply a validated per-pulse GAMM component manifest to one ERA5 grid day.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: predict_gamm_components.R MANIFEST GRID_CSV OUTPUT_DIR")
manifest_path <- args[[1]]
grid_path <- args[[2]]
output_dir <- args[[3]]

library(mgcv)
library(jsonlite)

manifest <- jsonlite::read_json(manifest_path, simplifyVector = FALSE)
grid <- utils::read.csv(grid_path, check.names = FALSE)
time_text <- sub("Z$", "", sub("\\.[0-9]+Z?$", "", grid$time_utc))
timestamps <- as.POSIXct(time_text, format = "%Y-%m-%dT%H:%M:%S", tz = "UTC")
if (any(is.na(timestamps))) stop("grid contains invalid UTC timestamps")
grid$day_of_year <- as.numeric(format(timestamps, "%j"))
grid$utc_hour <- as.numeric(format(timestamps, "%H"))

has_radar_effect <- function(model) {
  any(vapply(model$smooth, function(smooth) identical(smooth$label, "s(radar)"), logical(1)))
}

predict_component <- function(component, target) {
  model <- readRDS(component$model_rds)
  newdata <- grid
  if (has_radar_effect(model)) {
    reference <- model$model$radar[[1]]
    newdata$radar <- factor(as.character(reference), levels = levels(model$model$radar))
  }
  estimate <- stats::predict(
    model,
    newdata = newdata,
    exclude = if (has_radar_effect(model)) "s(radar)" else NULL,
    se.fit = TRUE
  )
  value <- as.numeric(estimate$fit)
  if (target == "mtr_birds_km_h") value <- pmax(value, 0)^2
  if (target == "vid_birds_per_km2") value <- pmax(value, 0)^3
  list(value = value, uncertainty = as.numeric(estimate$se.fit))
}

dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
for (pulse in names(manifest$components)) {
  output <- grid[, c("time_utc", "longitude", "latitude", "support"), drop = FALSE]
  for (target in names(manifest$components[[pulse]])) {
    prediction <- predict_component(manifest$components[[pulse]][[target]], target)
    output[[target]] <- prediction$value
    output[[paste0("uncertainty_", target)]] <- prediction$uncertainty
  }
  utils::write.csv(output, file.path(output_dir, sprintf("predictions_wide_%s.csv", pulse)), row.names = FALSE)
}

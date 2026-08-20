#!/usr/bin/env Rscript

# Apply a validated per-pulse GAMM component manifest to one ERA5 grid day.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: predict_gamm_components.R MANIFEST GRID_CSV OUTPUT_DIR")
manifest_path <- args[[1]]
grid_path <- args[[2]]
output_dir <- args[[3]]

library(mgcv)
library(jsonlite)

expected_manifest_sha256 <- Sys.getenv("BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256")
if (!grepl("^[0-9a-f]{64}$", expected_manifest_sha256)) {
  stop("BIRDCAST_UK_EXPECTED_COMPONENT_MANIFEST_SHA256 is missing or invalid")
}
manifest_hash_output <- system2("sha256sum", shQuote(manifest_path), stdout = TRUE, stderr = TRUE)
manifest_hash_status <- attr(manifest_hash_output, "status")
if (!is.null(manifest_hash_status) && manifest_hash_status != 0) {
  stop("sha256sum failed for the component manifest")
}
manifest_hash <- strsplit(manifest_hash_output[[1]], "[[:space:]]+")[[1]][[1]]
if (!identical(tolower(manifest_hash), expected_manifest_sha256)) {
  stop("component manifest hash differs from the reviewed authority")
}
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

resolve_model_path <- function(path) {
  if (grepl("^/", path)) path else file.path(dirname(manifest_path), path)
}

verify_component <- function(component) {
  path <- resolve_model_path(component$model_rds)
  if (!file.exists(path)) stop(sprintf("selected model is missing: %s", path))
  output <- system2("sha256sum", shQuote(path), stdout = TRUE, stderr = TRUE)
  status <- attr(output, "status")
  if (!is.null(status) && status != 0) stop(sprintf("sha256sum failed for %s", path))
  actual <- strsplit(output[[1]], "[[:space:]]+")[[1]][[1]]
  if (!identical(tolower(actual), tolower(component$sha256))) {
    stop(sprintf("selected model hash mismatch: %s", path))
  }
  path
}

back_transform <- function(value, transform) {
  if (identical(transform, "identity")) return(value)
  if (identical(transform, "square_nonnegative")) return(pmax(value, 0)^2)
  if (identical(transform, "cube_nonnegative")) return(pmax(value, 0)^3)
  stop(sprintf("unsupported prediction transform: %s", transform))
}

prediction_transform <- function(component, target) {
  # The reviewed manifest is hash-locked and predates descriptive transform
  # fields. Derive its established fit contract by target, while rejecting a
  # future manifest that tries to declare a different transform.
  expected <- switch(target,
    mtr_birds_km_h = "square_nonnegative",
    vid_birds_per_km2 = "cube_nonnegative",
    bird_u_ms = "identity",
    bird_v_ms = "identity",
    stop(sprintf("unsupported selected-model target: %s", target))
  )
  declared <- component$prediction_transform
  if (!is.null(declared) && !identical(declared, expected)) {
    stop(sprintf("selected component transform mismatch for %s", target))
  }
  expected
}

predict_component <- function(component, target) {
  model <- readRDS(verify_component(component))
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
  value <- back_transform(
    as.numeric(estimate$fit),
    prediction_transform(component, target)
  )
  list(value = value, uncertainty = as.numeric(estimate$se.fit))
}

output_parent <- dirname(output_dir)
dir.create(output_parent, recursive = TRUE, showWarnings = FALSE)
if (file.exists(output_dir)) stop(sprintf("prediction day is immutable and already exists: %s", output_dir))
staging_dir <- tempfile(pattern = paste0(".", basename(output_dir), "."), tmpdir = output_parent)
if (!dir.create(staging_dir)) stop(sprintf("could not create prediction staging directory: %s", staging_dir))
on.exit(unlink(staging_dir, recursive = TRUE, force = TRUE), add = TRUE)
for (pulse in names(manifest$components)) {
  output <- grid[, c("time_utc", "longitude", "latitude", "support"), drop = FALSE]
  for (target in names(manifest$components[[pulse]])) {
    prediction <- predict_component(manifest$components[[pulse]][[target]], target)
    output[[target]] <- prediction$value
    output[[paste0("uncertainty_", target)]] <- prediction$uncertainty
  }
  utils::write.csv(
    output,
    file.path(staging_dir, sprintf("predictions_wide_%s.csv", pulse)),
    row.names = FALSE
  )
}
writeLines(expected_manifest_sha256, file.path(staging_dir, "component-manifest.sha256"))
if (!file.rename(staging_dir, output_dir)) {
  stop(sprintf("could not atomically promote prediction day: %s", output_dir))
}

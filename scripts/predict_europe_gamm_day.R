#!/usr/bin/env Rscript

# Predict one UTC day from audited ERA5 grid features.  Fitting is separate so
# the complete European model year is never placed in one R data frame.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 3) stop("usage: predict_europe_gamm_day.R MODEL_DIR GRID.csv OUTPUT.csv.gz")
model_dir <- args[[1]]
grid_path <- args[[2]]
output_path <- args[[3]]

if (!requireNamespace("jsonlite", quietly = TRUE)) stop("required package missing: jsonlite")
metrics <- jsonlite::fromJSON(file.path(model_dir, "metrics.json"), simplifyVector = TRUE)
grid <- utils::read.csv(grid_path, check.names = FALSE)
required <- c("time_utc", "longitude", "latitude", "nearest_radar_km", metrics$predictors)
missing <- setdiff(required, names(grid))
if (length(missing)) stop(sprintf("grid day is missing: %s", paste(missing, collapse = ", ")))
if (!nrow(grid)) stop("grid day has no rows")
origin <- as.POSIXct(metrics$time_origin_utc, format = "%Y-%m-%dT%H:%M:%OSZ", tz = "UTC")
timestamps <- as.POSIXct(grid$time_utc, format = "%Y-%m-%dT%H:%M:%OSZ", tz = "UTC")
if (is.na(origin) || any(is.na(timestamps))) stop("grid day contains invalid UTC timestamps")
grid$time_index_hours <- as.numeric(difftime(timestamps, origin, units = "hours"))
grid$utc_hour <- as.integer(format(timestamps, "%H", tz = "UTC"))

prepare_frame <- function(frame, fit, reference_source) {
  training <- fit$data
  frame$source <- factor(rep(reference_source, nrow(frame)), levels = levels(training$source))
  frame$country <- factor(levels(training$country)[1], levels = levels(training$country))
  frame$network <- factor(levels(training$network)[1], levels = levels(training$network))
  frame$radar <- factor(levels(training$radar)[1], levels = levels(training$radar))
  frame
}

for (target in metrics$targets) {
  model_file <- metrics$model_files[[target]]
  if (!file.exists(model_file)) stop(sprintf("model file is missing for %s", target))
  fit <- readRDS(model_file)
  if (is.null(fit$model) || is.null(fit$data) || is.null(fit$random_terms)) {
    stop(sprintf("model file for %s does not retain the daily prediction contract", target))
  }
  prepared <- prepare_frame(grid, fit, metrics$reference_source)
  estimate <- stats::predict(fit$model, newdata = prepared, exclude = fit$random_terms, se.fit = TRUE)
  value <- as.numeric(estimate$fit)
  if (isTRUE(fit$intensity)) value <- pmax(expm1(value), 0)
  grid[[target]] <- value
  grid[[paste0("uncertainty_", target)]] <- as.numeric(estimate$se.fit)
}

grid$prediction_class <- ifelse(
  grid$nearest_radar_km <= 150, "interpolation",
  ifelse(grid$nearest_radar_km <= 250, "extrapolation", "unsupported")
)
if (any(grid$prediction_class == "unsupported")) stop("unsupported cells reached the daily predictor")
con <- gzfile(output_path, open = "wt")
on.exit(close(con), add = TRUE)
utils::write.csv(grid, con, row.names = FALSE)

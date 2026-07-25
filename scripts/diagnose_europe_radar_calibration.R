#!/usr/bin/env Rscript

# Quantify how much site-scale calibration is required by a fitted Europe GAMM.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 2) stop("usage: diagnose_europe_radar_calibration.R MODEL_DIR OUTPUT.json")
if (!requireNamespace("jsonlite", quietly = TRUE)) stop("required package missing: jsonlite")
metrics <- jsonlite::fromJSON(file.path(args[[1]], "metrics.json"), simplifyVector = TRUE)

score <- function(observed, predicted) {
  keep <- is.finite(observed) & is.finite(predicted) & observed >= 0 & predicted >= 0
  if (sum(keep) < 30) return(NULL)
  observed <- log1p(observed[keep])
  predicted <- log1p(predicted[keep])
  list(
    row_count = length(observed),
    log1p_r_squared = if (stats::var(observed) > 0) 1 - sum((predicted - observed)^2) /
      sum((observed - mean(observed))^2) else NA_real_,
    correlation = stats::cor(observed, predicted)
  )
}

results <- list()
for (target in intersect(metrics$targets, c("mtr_birds_km_h", "vid_birds_per_km2"))) {
  fit <- readRDS(metrics$model_files[[target]])
  frame <- fit$data
  included <- stats::predict(fit$model, newdata = frame)
  excluded <- stats::predict(fit$model, newdata = frame, exclude = fit$random_terms)
  if (isTRUE(fit$intensity)) {
    included <- pmax(expm1(included), 0)
    excluded <- pmax(expm1(excluded), 0)
  }
  rows <- lapply(sort(unique(as.character(frame$radar))), function(radar) {
    index <- as.character(frame$radar) == radar
    list(
      radar = radar,
      with_radar_calibration = score(frame[[target]][index], included[index]),
      without_radar_calibration = score(frame[[target]][index], excluded[index])
    )
  })
  results[[target]] <- rows
}
jsonlite::write_json(
  list(
    schema_version = "birdcast-euro-radar-calibration-diagnostic-1.0",
    model_id = metrics$model_id,
    calibration_is_in_sample_only = TRUE,
    targets = results
  ), args[[2]], auto_unbox = TRUE, pretty = TRUE
)

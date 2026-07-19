#!/usr/bin/env Rscript

# Fit the primary, interpretable ERA5 GAMM.  Execute this in the project
# Apptainer image on JASMIN; it is intentionally not run on the web host.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: fit_gamm.R MODEL_SPEC.json OUTPUT_DIR [GRID.csv]")
spec_path <- args[[1]]
output_dir <- args[[2]]
grid_path <- if (length(args) >= 3) args[[3]] else NULL

for (pkg in c("mgcv", "jsonlite")) {
  if (!requireNamespace(pkg, quietly = TRUE)) stop(sprintf("required package missing: %s", pkg))
}
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
spec <- jsonlite::fromJSON(spec_path, simplifyVector = TRUE)
data <- utils::read.csv(spec$training_csv, check.names = FALSE)
data$radar <- factor(data$radar)
predictors <- spec$predictors
predictors <- predictors[predictors %in% names(data)]
if (!all(c("easting_m", "northing_m") %in% predictors)) stop("projected spatial predictors are required")
smooth_features <- setdiff(predictors, c("easting_m", "northing_m"))
targets <- c(spec$intensity_targets, spec$vector_targets)

metric_rows <- list()
row_id <- 0
prediction_grid <- NULL
if (!is.null(grid_path) && file.exists(grid_path)) {
  prediction_grid <- utils::read.csv(grid_path, check.names = FALSE)
  grid_columns <- c("time_utc", "longitude", "latitude", "support")
  if (!all(c(grid_columns, predictors) %in% names(prediction_grid))) {
    stop("national ERA5 grid must include time_utc, coordinates, support, and all predictors")
  }
}

score <- function(observed, predicted) {
  residual <- predicted - observed
  threshold <- as.numeric(stats::quantile(observed, .9, na.rm = TRUE, names = FALSE))
  predicted_event <- predicted >= threshold
  observed_event <- observed >= threshold
  precision <- if (sum(predicted_event) > 0) sum(predicted_event & observed_event) / sum(predicted_event) else 0
  recall <- if (sum(observed_event) > 0) sum(predicted_event & observed_event) / sum(observed_event) else 0
  list(
    rmse = sqrt(mean(residual^2)), mae = mean(abs(residual)), bias = mean(residual),
    r_squared = if (stats::var(observed) > 0) 1 - sum(residual^2) / sum((observed - mean(observed))^2) else 0,
    top_decile_precision = precision, top_decile_recall = recall
  )
}

fit_formula <- function(target, variables) {
  terms <- c(
    "s(easting_m, northing_m, bs='tp', k=10)",
    sprintf("s(%s, bs='tp')", variables),
    "s(radar, bs='re')"
  )
  stats::as.formula(sprintf("response ~ %s", paste(terms, collapse = " + ")))
}

blocked_time_split <- function(data) {
  times <- sort(unique(as.character(data$time_utc)))
  if (length(times) < 10) return(NULL)
  cut_index <- max(1, floor(length(times) * .8))
  cutoff <- times[[cut_index]]
  train <- data[as.character(data$time_utc) <= cutoff, , drop = FALSE]
  test <- data[as.character(data$time_utc) > cutoff, , drop = FALSE]
  if (nrow(train) < 30 || !nrow(test)) return(NULL)
  list(train = train, test = test, cutoff = cutoff)
}

for (pulse in spec$pulses) {
  pulse_data <- data[data$pulse == pulse, , drop = FALSE]
  pulse_prediction <- NULL
  if (!is.null(prediction_grid)) {
    pulse_prediction <- prediction_grid[, c("time_utc", "longitude", "latitude", "support"), drop = FALSE]
  }
  for (target in targets) {
    required <- unique(c("radar", target, predictors))
    subset <- pulse_data[stats::complete.cases(pulse_data[, required, drop = FALSE]), , drop = FALSE]
    if (nrow(subset) < 30 || length(unique(subset$radar)) < 2) next
    is_intensity <- target %in% spec$intensity_targets
    subset$response <- if (is_intensity) pmax(subset[[target]], 0)^(1 / 3) else subset[[target]]
    weights <- if (is_intensity) pmax(subset$profile_count, 1) else pmax(subset$mtr_birds_km_h, 0.01)
    formula <- fit_formula(target, smooth_features)
    held_out <- list()
    for (held_radar in unique(subset$radar)) {
      train <- subset[subset$radar != held_radar, , drop = FALSE]
      test <- subset[subset$radar == held_radar, , drop = FALSE]
      if (nrow(train) < 30 || !nrow(test)) next
      model <- mgcv::bam(formula, data = train, weights = weights[subset$radar != held_radar], method = "fREML", discrete = TRUE)
      predicted <- as.numeric(stats::predict(model, newdata = test, exclude = "s(radar)"))
      if (is_intensity) predicted <- pmax(predicted, 0)^3
      held_out[[length(held_out) + 1]] <- data.frame(observed = test[[target]], predicted = predicted)
    }
    if (!length(held_out)) next
    validated <- do.call(rbind, held_out)
    metrics <- score(validated$observed, validated$predicted)
    row_id <- row_id + 1
    metric_rows[[row_id]] <- c(list(pulse = pulse, target = target, validation = "leave_one_radar_out", row_count = nrow(validated)), metrics)
    blocked <- blocked_time_split(subset)
    if (!is.null(blocked)) {
      time_model <- mgcv::bam(
        formula, data = blocked$train,
        weights = if (is_intensity) pmax(blocked$train$profile_count, 1) else pmax(blocked$train$mtr_birds_km_h, 0.01),
        method = "fREML", discrete = TRUE
      )
      time_predicted <- as.numeric(stats::predict(time_model, newdata = blocked$test, exclude = "s(radar)"))
      if (is_intensity) time_predicted <- pmax(time_predicted, 0)^3
      time_metrics <- score(blocked$test[[target]], time_predicted)
      row_id <- row_id + 1
      metric_rows[[row_id]] <- c(
        list(pulse = pulse, target = target, validation = "blocked_time", row_count = nrow(blocked$test), cutoff_time_utc = blocked$cutoff),
        time_metrics
      )
    }
    final_model <- mgcv::bam(formula, data = subset, weights = weights, method = "fREML", discrete = TRUE)
    saveRDS(final_model, file.path(output_dir, sprintf("gamm_%s_%s.rds", pulse, target)))
    if (!is.null(prediction_grid)) {
      # mgcv still requires every formula variable in newdata even when the
      # radar random effect is excluded from the national prediction.
      prediction_data <- prediction_grid
      prediction_data$radar <- factor(
        as.character(subset$radar[[1]]),
        levels = levels(factor(subset$radar))
      )
      estimate <- stats::predict(
        final_model,
        newdata = prediction_data,
        exclude = "s(radar)",
        se.fit = TRUE
      )
      value <- as.numeric(estimate$fit)
      if (is_intensity) value <- pmax(value, 0)^3
      pulse_prediction[[target]] <- value
      pulse_prediction[[sprintf("uncertainty_%s", target)]] <- as.numeric(estimate$se.fit)
    }
  }
  if (!is.null(pulse_prediction)) {
    if (!all(targets %in% names(pulse_prediction))) {
      stop(sprintf("missing national predictions for pulse %s", pulse))
    }
    utils::write.csv(
      pulse_prediction,
      file.path(output_dir, sprintf("predictions_wide_%s.csv", pulse)),
      row.names = FALSE
    )
  }
}

jsonlite::write_json(list(model_family = "gamm", metrics = metric_rows, model_time_terms = "none", predictors = predictors), file.path(output_dir, "metrics.json"), auto_unbox = TRUE, pretty = TRUE)

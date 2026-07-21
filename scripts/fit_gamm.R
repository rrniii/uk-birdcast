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
# The CSV uses ISO-8601 UTC timestamps. Supplying the format is essential:
# as.POSIXct's default parser accepts the date while silently dropping hours.
timestamps <- as.POSIXct(data$time_utc, format = "%Y-%m-%dT%H:%M:%OSZ", tz = "UTC")
if (any(is.na(timestamps))) stop("training table contains invalid UTC timestamps")
data$day_of_year <- as.numeric(format(timestamps, "%j"))
data$utc_hour <- as.numeric(format(timestamps, "%H"))
threads <- max(1L, as.integer(Sys.getenv("SLURM_CPUS_PER_TASK", "1")))
options <- spec$gamm_options
if (is.null(options)) options <- list()
intensity_transform <- if (!is.null(options$intensity_transform)) options$intensity_transform else "cube_root"
intensity_weights <- if (!is.null(options$intensity_weights)) options$intensity_weights else "profile_count"
intensity_weight_power <- if (!is.null(options$intensity_weight_power)) as.numeric(options$intensity_weight_power) else NULL
spatial_k <- if (!is.null(options$spatial_k)) as.integer(options$spatial_k) else 10L
covariate_k <- if (!is.null(options$covariate_k)) as.integer(options$covariate_k) else NULL
interactions <- if (!is.null(options$meteorology_interactions)) unlist(options$meteorology_interactions) else character()
temporal_smooths <- if (!is.null(options$temporal_smooths)) unlist(options$temporal_smooths) else character()
requested_targets <- if (!is.null(options$targets)) unlist(options$targets) else NULL
if (!(intensity_transform %in% c("cube_root", "sqrt", "log1p"))) stop("unsupported intensity_transform")
if (!(intensity_weights %in% c("profile_count", "uniform", "sqrt_mtr", "mtr", "mtr_power"))) stop("unsupported intensity_weights")
if (intensity_weights == "mtr_power" && (is.null(intensity_weight_power) || !is.finite(intensity_weight_power) || intensity_weight_power < 0 || intensity_weight_power > 1)) {
  stop("mtr_power intensity weighting requires intensity_weight_power in [0, 1]")
}
if (spatial_k < 3) stop("spatial_k must be at least 3")
temporal_knots <- if (length(temporal_smooths)) list(day_of_year = c(0.5, 366.5), utc_hour = c(-0.5, 23.5)) else NULL
predictors <- spec$predictors
missing_predictors <- setdiff(predictors, names(data))
if (length(missing_predictors)) {
  stop(sprintf("training table is missing declared predictors: %s", paste(missing_predictors, collapse = ", ")))
}
if (!all(c("easting_m", "northing_m") %in% predictors)) stop("projected spatial predictors are required")
smooth_features <- setdiff(predictors, c("easting_m", "northing_m"))
targets <- c(spec$intensity_targets, spec$vector_targets)
if (!is.null(requested_targets)) {
  if (!all(requested_targets %in% targets)) stop("gamm_options targets must be declared model targets")
  targets <- requested_targets
}

metric_rows <- list()
row_id <- 0
fold_metric_rows <- list()
fold_row_id <- 0
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
  smooth_terms <- if (is.null(covariate_k)) {
    sprintf("s(%s, bs='tp')", variables)
  } else {
    sprintf("s(%s, bs='tp', k=%d)", variables, covariate_k)
  }
  terms <- c(
    sprintf("s(easting_m, northing_m, bs='tp', k=%d)", spatial_k),
    smooth_terms,
    "s(radar, bs='re')"
  )
  valid_interactions <- c(
    wind_850 = "ti(u_850_ms, v_850_ms, bs=c('tp','tp'), k=c(6,6))",
    thermal_moisture_850 = "ti(temperature_850_k, relative_humidity_850_percent, bs=c('tp','tp'), k=c(6,6))"
  )
  unknown_interactions <- setdiff(interactions, names(valid_interactions))
  if (length(unknown_interactions)) stop(sprintf("unsupported meteorology interaction: %s", paste(unknown_interactions, collapse=", ")))
  terms <- c(terms, unname(valid_interactions[interactions]))
  valid_temporal_smooths <- c(
    day_of_year = "s(day_of_year, bs='cc', k=20)",
    utc_hour = "s(utc_hour, bs='cc', k=12)"
  )
  unknown_temporal_smooths <- setdiff(temporal_smooths, names(valid_temporal_smooths))
  if (length(unknown_temporal_smooths)) stop(sprintf("unsupported temporal smooth: %s", paste(unknown_temporal_smooths, collapse=", ")))
  terms <- c(terms, unname(valid_temporal_smooths[temporal_smooths]))
  stats::as.formula(sprintf("response ~ %s", paste(terms, collapse = " + ")))
}

transform_intensity <- function(values) {
  values <- pmax(values, 0)
  switch(intensity_transform,
    cube_root = values^(1 / 3),
    sqrt = sqrt(values),
    log1p = log1p(values)
  )
}

inverse_intensity <- function(values) {
  values <- pmax(values, 0)
  switch(intensity_transform,
    cube_root = values^3,
    sqrt = values^2,
    log1p = expm1(values)
  )
}

intensity_weight <- function(frame) {
  switch(intensity_weights,
    profile_count = pmax(frame$profile_count, 1),
    uniform = rep(1, nrow(frame)),
    sqrt_mtr = sqrt(pmax(frame$mtr_birds_km_h, 0.01)),
    mtr_power = pmax(frame$mtr_birds_km_h, 0.01)^intensity_weight_power,
    mtr = pmax(frame$mtr_birds_km_h, 0.01)
  )
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
    subset$response <- if (is_intensity) transform_intensity(subset[[target]]) else subset[[target]]
    weights <- if (is_intensity) intensity_weight(subset) else pmax(subset$mtr_birds_km_h, 0.01)
    formula <- fit_formula(target, smooth_features)
    held_out <- list()
    for (held_radar in unique(subset$radar)) {
      train <- subset[subset$radar != held_radar, , drop = FALSE]
      test <- subset[subset$radar == held_radar, , drop = FALSE]
      if (nrow(train) < 30 || !nrow(test)) next
      model <- mgcv::bam(
        formula, data = train, weights = weights[subset$radar != held_radar],
        method = "fREML", discrete = TRUE, nthreads = threads,
        knots = temporal_knots
      )
      # The radar random effect is excluded for spatial transfer. Give mgcv a
      # level present in the fitted data so it does not warn about the
      # intentionally held-out factor level.
      prediction_test <- test
      prediction_test$radar <- factor(
        rep(as.character(train$radar[[1]]), nrow(test)),
        levels = levels(train$radar)
      )
      predicted <- as.numeric(stats::predict(model, newdata = prediction_test, exclude = "s(radar)"))
      if (is_intensity) predicted <- inverse_intensity(predicted)
      held_out[[length(held_out) + 1]] <- data.frame(observed = test[[target]], predicted = predicted)
      fold_row_id <- fold_row_id + 1
      fold_metric_rows[[fold_row_id]] <- c(
        list(
          pulse = pulse, target = target, validation = "leave_one_radar_out",
          held_out_radar = as.character(held_radar), row_count = nrow(test)
        ),
        score(test[[target]], predicted)
      )
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
        weights = if (is_intensity) intensity_weight(blocked$train) else pmax(blocked$train$mtr_birds_km_h, 0.01),
        method = "fREML", discrete = TRUE, nthreads = threads,
        knots = temporal_knots
      )
      time_predicted <- as.numeric(stats::predict(time_model, newdata = blocked$test, exclude = "s(radar)"))
      if (is_intensity) time_predicted <- inverse_intensity(time_predicted)
      time_metrics <- score(blocked$test[[target]], time_predicted)
      row_id <- row_id + 1
      metric_rows[[row_id]] <- c(
        list(pulse = pulse, target = target, validation = "blocked_time", row_count = nrow(blocked$test), cutoff_time_utc = blocked$cutoff),
        time_metrics
      )
    }
    final_model <- mgcv::bam(
      formula, data = subset, weights = weights,
      method = "fREML", discrete = TRUE, nthreads = threads,
      knots = temporal_knots
    )
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
      if (is_intensity) value <- inverse_intensity(value)
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

jsonlite::write_json(list(
  model_family = "gamm", metrics = metric_rows,
  model_time_terms = if (length(temporal_smooths)) temporal_smooths else "none",
  fold_metrics = fold_metric_rows,
  predictors = predictors,
  gamm_options = list(
    intensity_transform = intensity_transform, intensity_weights = intensity_weights,
    intensity_weight_power = intensity_weight_power,
    spatial_k = spatial_k, covariate_k = covariate_k, meteorology_interactions = interactions,
    temporal_smooths = temporal_smooths,
    targets = targets
  )
), file.path(output_dir, "metrics.json"), auto_unbox = TRUE, pretty = TRUE)

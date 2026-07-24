#!/usr/bin/env Rscript

# Fit one Europe-wide, source-aware GAMM on streamed Aloft hourly derivatives
# and the existing UK SP archive. Aloft BALTRAD is the prediction reference.

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 2) stop("usage: fit_europe_gamm.R MODEL_SPEC.json OUTPUT_DIR [GRID.csv]")
spec_path <- args[[1]]
output_dir <- args[[2]]
grid_path <- if (length(args) >= 3) args[[3]] else NULL

for (pkg in c("mgcv", "jsonlite")) {
  if (!requireNamespace(pkg, quietly = TRUE)) stop(sprintf("required package missing: %s", pkg))
}
library(mgcv)
dir.create(output_dir, recursive = TRUE, showWarnings = FALSE)
spec <- jsonlite::fromJSON(spec_path, simplifyVector = TRUE)
data <- utils::read.csv(spec$training_csv, check.names = FALSE)
transfer_validation <- if (
  !is.null(spec$validation_csv) && file.exists(spec$validation_csv)
) {
  utils::read.csv(spec$validation_csv, check.names = FALSE)
} else NULL

required_base <- c(
  "radar", "source", "network", "country", "time_utc",
  "easting_m", "northing_m", spec$predictors
)
missing <- setdiff(c(required_base, spec$targets), names(data))
if (length(missing)) stop(sprintf("training table is missing: %s", paste(missing, collapse = ", ")))
if (any(data$pulse == "lp", na.rm = TRUE)) stop("Europe training table must not contain UK LP observations")
if (!all(unique(data$source) %in% c("aloft-baltrad", "jasmin-uk-sp"))) {
  stop("Europe model accepts only aloft-baltrad and jasmin-uk-sp observations")
}

data$source <- stats::relevel(factor(data$source), ref = spec$reference_source)
data$radar <- factor(data$radar)
data$network <- factor(data$network)
data$country <- factor(data$country)
data$.row_id <- seq_len(nrow(data))
timestamps <- as.POSIXct(data$time_utc, format = "%Y-%m-%dT%H:%M:%OSZ", tz = "UTC")
if (any(is.na(timestamps))) stop("training table contains invalid UTC timestamps")

threads <- max(1L, as.integer(Sys.getenv("SLURM_CPUS_PER_TASK", "1")))
spatial_k <- if (!is.null(spec$spatial_k)) as.integer(spec$spatial_k) else 40L
covariate_k <- if (!is.null(spec$covariate_k)) as.integer(spec$covariate_k) else 8L
if (spatial_k < 3 || covariate_k < 3) stop("smooth basis dimensions must be at least 3")

site_counts <- table(data$radar)
data$site_equal_weight <- 1 / as.numeric(site_counts[data$radar])
data$site_equal_weight <- data$site_equal_weight / mean(data$site_equal_weight)

score <- function(observed, predicted) {
  keep <- is.finite(observed) & is.finite(predicted)
  observed <- observed[keep]
  predicted <- predicted[keep]
  if (!length(observed)) return(list(row_count = 0))
  residual <- predicted - observed
  threshold <- as.numeric(stats::quantile(observed, .9, na.rm = TRUE, names = FALSE))
  observed_event <- observed >= threshold
  predicted_event <- predicted >= threshold
  tp <- sum(observed_event & predicted_event)
  precision <- if (sum(predicted_event)) tp / sum(predicted_event) else 0
  recall <- if (sum(observed_event)) tp / sum(observed_event) else 0
  list(
    row_count = length(observed),
    rmse = sqrt(mean(residual^2)),
    mae = mean(abs(residual)),
    bias = mean(residual),
    r_squared = if (stats::var(observed) > 0) {
      1 - sum(residual^2) / sum((observed - mean(observed))^2)
    } else 0,
    log1p_r_squared = if (
      all(observed >= 0) && all(predicted >= 0) &&
      stats::var(log1p(observed)) > 0
    ) {
      1 - sum((log1p(predicted) - log1p(observed))^2) /
        sum((log1p(observed) - mean(log1p(observed)))^2)
    } else NA_real_,
    top_decile_precision = precision,
    top_decile_recall = recall,
    top_decile_f1 = if (precision + recall) 2 * precision * recall / (precision + recall) else 0
  )
}

model_formula <- function(frame) {
  terms <- c(
    sprintf("s(easting_m, northing_m, bs='tp', k=%d)", spatial_k),
    sprintf("s(%s, bs='tp', k=%d)", spec$predictors, covariate_k)
  )
  if (length(unique(frame$source)) > 1) terms <- c("source", terms)
  random <- character()
  for (name in c("country", "network", "radar")) {
    if (length(unique(frame[[name]])) > 1) {
      terms <- c(terms, sprintf("s(%s, bs='re')", name))
      random <- c(random, sprintf("s(%s)", name))
    }
  }
  list(formula = stats::as.formula(sprintf("response ~ %s", paste(terms, collapse = " + "))), random = random)
}

prediction_frame <- function(frame, training, reference_source = FALSE) {
  frame$source <- factor(
    if (reference_source) rep(spec$reference_source, nrow(frame)) else as.character(frame$source),
    levels = levels(training$source)
  )
  frame$country <- factor(levels(training$country)[1], levels = levels(training$country))
  frame$network <- factor(levels(training$network)[1], levels = levels(training$network))
  frame$radar <- factor(levels(training$radar)[1], levels = levels(training$radar))
  frame
}

fit_target <- function(frame, target) {
  is_intensity <- target %in% spec$intensity_targets
  complete <- stats::complete.cases(frame[, unique(c(required_base, target)), drop = FALSE])
  subset <- frame[complete, , drop = FALSE]
  if (nrow(subset) < 100 || length(unique(subset$radar)) < 3) {
    stop(sprintf("insufficient complete rows for %s", target))
  }
  subset$source <- droplevels(factor(subset$source))
  subset$radar <- droplevels(factor(subset$radar))
  subset$network <- droplevels(factor(subset$network))
  subset$country <- droplevels(factor(subset$country))
  subset$response <- if (is_intensity) log1p(pmax(subset[[target]], 0)) else subset[[target]]
  formula_info <- model_formula(subset)
  model <- mgcv::bam(
    formula_info$formula, data = subset, weights = site_equal_weight,
    method = "fREML", discrete = TRUE, nthreads = threads, family = gaussian()
  )
  list(model = model, data = subset, intensity = is_intensity, random_terms = formula_info$random)
}

predict_target <- function(fit, frame, reference_source = FALSE) {
  prepared <- prediction_frame(frame, fit$data, reference_source)
  estimate <- stats::predict(fit$model, newdata = prepared, exclude = fit$random_terms, se.fit = TRUE)
  value <- as.numeric(estimate$fit)
  if (fit$intensity) value <- pmax(expm1(value), 0)
  list(value = value, uncertainty = as.numeric(estimate$se.fit))
}

fold_rows <- list()
vector_fold_rows <- list()
fold_id <- 0
evaluate_groups <- function(target, group_name, max_groups = Inf) {
  groups <- sort(unique(as.character(data[[group_name]])))
  if (is.finite(max_groups)) groups <- head(groups, max_groups)
  for (held in groups) {
    train <- data[as.character(data[[group_name]]) != held, , drop = FALSE]
    test <- data[as.character(data[[group_name]]) == held, , drop = FALSE]
    if (nrow(test) < 30 || length(unique(train$radar)) < 3) next
    if (!all(unique(test$source) %in% unique(train$source))) next
    fit <- fit_target(train, target)
    prediction <- predict_target(fit, test)$value
    fold_id <<- fold_id + 1
    fold_rows[[fold_id]] <<- c(
      list(target = target, validation = paste0("leave_one_", group_name, "_out"), held_out = held),
      score(test[[target]], prediction)
    )
    if (group_name == "radar" && target %in% spec$vector_targets) {
      vector_fold_rows[[length(vector_fold_rows) + 1]] <<- data.frame(
        row_id = test$.row_id,
        radar = as.character(test$radar),
        time_utc = test$time_utc,
        target = target,
        observed = test[[target]],
        predicted = prediction
      )
    }
  }
}

model_files <- list()
grid <- if (!is.null(grid_path) && file.exists(grid_path)) {
  utils::read.csv(grid_path, check.names = FALSE)
} else NULL
predictions <- if (!is.null(grid)) grid[, c("time_utc", "longitude", "latitude", "support", "nearest_radar_km")] else NULL

prepare_external <- function(frame) {
  frame$source <- factor(frame$source, levels = levels(data$source))
  frame$radar <- factor(frame$radar)
  frame$network <- factor(frame$network)
  frame$country <- factor(frame$country)
  frame
}
if (!is.null(transfer_validation)) {
  external_missing <- setdiff(c(required_base, spec$targets), names(transfer_validation))
  if (length(external_missing)) {
    stop(sprintf(
      "transfer-validation table is missing: %s",
      paste(external_missing, collapse = ", ")
    ))
  }
  if (!all(unique(transfer_validation$source) %in% levels(data$source))) {
    stop("transfer-validation data contains an unseen source")
  }
  transfer_validation <- prepare_external(transfer_validation)
}

for (target in spec$targets) {
  evaluate_groups(target, "radar", if (!is.null(spec$max_radar_folds)) spec$max_radar_folds else Inf)
  evaluate_groups(target, "country")
  evaluate_groups(target, "network")

  ordered_times <- sort(unique(data$time_utc))
  cutoff <- ordered_times[[max(1, floor(length(ordered_times) * .8))]]
  train <- data[data$time_utc <= cutoff, , drop = FALSE]
  test <- data[data$time_utc > cutoff, , drop = FALSE]
  if (nrow(test) >= 30 && length(unique(train$radar)) >= 3) {
    temporal_fit <- fit_target(train, target)
    temporal_prediction <- predict_target(temporal_fit, test)$value
    fold_id <- fold_id + 1
    fold_rows[[fold_id]] <- c(
      list(target = target, validation = "blocked_time", held_out = cutoff),
      score(test[[target]], temporal_prediction)
    )
  }

  final <- fit_target(data, target)
  if (!is.null(transfer_validation)) {
    for (held in sort(unique(as.character(transfer_validation$radar)))) {
      test <- transfer_validation[
        as.character(transfer_validation$radar) == held &
          is.finite(transfer_validation[[target]]),
        ,
        drop = FALSE
      ]
      if (nrow(test) < 30) next
      external_prediction <- predict_target(final, test)$value
      fold_id <- fold_id + 1
      fold_rows[[fold_id]] <- c(
        list(target = target, validation = "transfer_validation", held_out = held),
        score(test[[target]], external_prediction)
      )
    }
  }
  model_file <- file.path(output_dir, sprintf("gamm_europe_%s.rds", target))
  saveRDS(final$model, model_file)
  model_files[[target]] <- model_file
  if (!is.null(grid)) {
    grid$source <- spec$reference_source
    estimated <- predict_target(final, grid, reference_source = TRUE)
    predictions[[target]] <- estimated$value
    predictions[[paste0("uncertainty_", target)]] <- estimated$uncertainty
  }
}

if (!is.null(predictions)) {
  predictions$prediction_class <- ifelse(
    predictions$nearest_radar_km <= spec$interpolation_distance_km,
    "interpolation",
    ifelse(predictions$nearest_radar_km <= spec$maximum_support_distance_km, "extrapolation", "unsupported")
  )
  predictions <- predictions[predictions$prediction_class != "unsupported", , drop = FALSE]
  utils::write.csv(predictions, file.path(output_dir, "predictions_wide_europe.csv"), row.names = FALSE)
}

vector_fold_path <- file.path(output_dir, "heldout_radar_vectors.csv")
if (length(vector_fold_rows)) {
  utils::write.csv(do.call(rbind, vector_fold_rows), vector_fold_path, row.names = FALSE)
}

jsonlite::write_json(
  list(
    schema_version = "birdcast-euro-gamm-metrics-1.0",
    model_id = spec$model_id,
    model_family = "source-aware-gamm",
    reference_source = spec$reference_source,
    sources = sort(unique(as.character(data$source))),
    model_time_terms = "none",
    site_equal_weighting = TRUE,
    folds = fold_rows,
    heldout_radar_vectors = if (file.exists(vector_fold_path)) vector_fold_path else NULL,
    model_files = model_files,
    predictors = spec$predictors,
    targets = spec$targets
  ),
  file.path(output_dir, "metrics.json"),
  auto_unbox = TRUE,
  pretty = TRUE
)

#!/usr/bin/env Rscript

# Quantify a geographically defined UK coastal-corridor MTR GAMM with three
# spatial-transfer tests: continental leave-one-radar-out, continent-to-UK,
# and UK-to-continent.  It deliberately does not apply site calibration.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) != 5) stop("usage: probe_uk_coastal_corridor.R SPEC.json COHORT.json TIME_K OUTPUT.json TARGET")
if (!requireNamespace("mgcv", quietly=TRUE) || !requireNamespace("jsonlite", quietly=TRUE)) {
  stop("mgcv and jsonlite are required")
}
library(mgcv)

spec <- jsonlite::fromJSON(args[[1]], simplifyVector=TRUE)
cohort <- jsonlite::fromJSON(args[[2]], simplifyVector=TRUE)
time_k <- as.integer(args[[3]])
target <- args[[5]]
data <- utils::read.csv(spec$training_csv, check.names=FALSE)
radar_values <- function(value) {
  if (is.data.frame(value)) return(as.character(value$radar))
  vapply(value, function(row) row$radar, character(1))
}
continental_radars <- radar_values(cohort$continental_radars)
uk_radars <- radar_values(cohort$uk_radars)
if (!all(c(continental_radars, uk_radars) %in% data$radar)) stop("cohort radar missing from corridor training table")
if (length(continental_radars) < 3 || length(uk_radars) < 3) stop("each corridor side requires at least three radars")

required <- c("radar", "source", "network", "country", "time_utc", "easting_m", "northing_m", spec$predictors, target)
if (length(setdiff(required, names(data)))) stop("corridor training table is missing required fields")
timestamps <- as.POSIXct(data$time_utc, format="%Y-%m-%dT%H:%M:%OSZ", tz="UTC")
if (any(is.na(timestamps))) stop("corridor training table has invalid UTC timestamps")
origin <- min(timestamps)

score <- function(observed, predicted) {
  keep <- is.finite(observed) & is.finite(predicted) & observed >= 0 & predicted >= 0
  observed <- observed[keep]; predicted <- predicted[keep]
  if (length(observed) < 30) return(NULL)
  log_r2 <- if (stats::var(log1p(observed)) > 0) {
    1 - sum((log1p(predicted) - log1p(observed))^2) / sum((log1p(observed) - mean(log1p(observed)))^2)
  } else NA_real_
  observed_event <- observed >= stats::quantile(observed, .9)
  predicted_event <- predicted >= stats::quantile(predicted, .9)
  true_positive <- sum(observed_event & predicted_event)
  precision <- if (sum(predicted_event)) true_positive / sum(predicted_event) else 0
  recall <- if (sum(observed_event)) true_positive / sum(observed_event) else 0
  list(
    row_count=length(observed),
    log1p_r_squared=log_r2,
    top_decile_f1=if (precision + recall) 2 * precision * recall / (precision + recall) else 0
  )
}

prepare_training <- function(frame) {
  stamps <- as.POSIXct(frame$time_utc, format="%Y-%m-%dT%H:%M:%OSZ", tz="UTC")
  frame$time_index_hours <- as.numeric(difftime(stamps, origin, units="hours"))
  frame$utc_hour <- as.integer(format(stamps, "%H", tz="UTC"))
  frame$source <- factor(frame$source)
  frame$country <- factor(frame$country)
  frame$network <- factor(frame$network)
  frame$radar <- factor(frame$radar)
  complete <- stats::complete.cases(frame[, unique(c(required, "time_index_hours", "utc_hour")), drop=FALSE])
  frame <- frame[complete, , drop=FALSE]
  frame$response <- log1p(pmax(frame[[target]], 0))
  counts <- table(frame$radar)
  frame$site_equal_weight <- 1 / as.numeric(counts[frame$radar])
  frame$site_equal_weight <- frame$site_equal_weight / mean(frame$site_equal_weight)
  frame
}

prepare_test <- function(frame, training, force_reference_source=FALSE) {
  stamps <- as.POSIXct(frame$time_utc, format="%Y-%m-%dT%H:%M:%OSZ", tz="UTC")
  frame$time_index_hours <- as.numeric(difftime(stamps, origin, units="hours"))
  frame$utc_hour <- as.integer(format(stamps, "%H", tz="UTC"))
  frame <- frame[stats::complete.cases(frame[, unique(c(required, "time_index_hours", "utc_hour")), drop=FALSE]), , drop=FALSE]
  frame$.original_radar <- as.character(frame$radar)
  source_values <- as.character(frame$source)
  if (force_reference_source || any(!source_values %in% levels(training$source))) {
    source_values <- rep(levels(training$source)[1], nrow(frame))
  }
  frame$source <- factor(source_values, levels=levels(training$source))
  # These random effects are excluded from every held-out prediction, so use a
  # valid reference level solely to meet mgcv's newdata factor contract.
  frame$country <- factor(rep(levels(training$country)[1], nrow(frame)), levels=levels(training$country))
  frame$network <- factor(rep(levels(training$network)[1], nrow(frame)), levels=levels(training$network))
  frame$radar <- factor(rep(levels(training$radar)[1], nrow(frame)), levels=levels(training$radar))
  frame
}

fit_and_predict <- function(train_raw, test_raw, force_reference_source=FALSE) {
  training <- prepare_training(train_raw)
  if (nrow(training) < 100 || length(unique(training$radar)) < 3) stop("insufficient training data for corridor fold")
  spatial_k <- min(20L, max(5L, length(unique(training$radar)) - 1L))
  terms <- c(
    sprintf("s(easting_m,northing_m,bs='tp',k=%d)", spatial_k),
    sprintf("s(%s,bs='tp',k=8)", spec$predictors),
    sprintf("s(time_index_hours,bs='cr',k=%d)", time_k),
    "s(utc_hour,bs='cc',k=12)"
  )
  if (length(unique(training$source)) > 1) terms <- c("source", terms)
  random_terms <- character()
  for (name in c("country", "network", "radar")) if (length(unique(training[[name]])) > 1) {
    terms <- c(terms, sprintf("s(%s,bs='re')", name))
    random_terms <- c(random_terms, sprintf("s(%s)", name))
  }
  model <- mgcv::bam(
    stats::as.formula(paste("response ~", paste(terms, collapse=" + "))),
    data=training, weights=site_equal_weight, method="fREML", discrete=TRUE, nthreads=1
  )
  test <- prepare_test(test_raw, training, force_reference_source)
  prediction <- pmax(expm1(stats::predict(model, newdata=test, exclude=random_terms)), 0)
  list(test=test, prediction=prediction, spatial_k=spatial_k, training_radar_count=length(unique(training$radar)))
}

site_rows <- function(test, prediction, validation, held_side) {
  radar_id <- test$.original_radar
  lapply(sort(unique(radar_id)), function(radar) {
    index <- radar_id == radar
    metric <- score(test[[target]][index], prediction[index])
    if (is.null(metric)) return(NULL)
    c(list(validation=validation, held_side=held_side, radar=radar), metric)
  })
}

run_fold <- function(train_raw, test_raw, validation, held_side, force_reference_source=FALSE) {
  result <- fit_and_predict(train_raw, test_raw, force_reference_source)
  rows <- site_rows(result$test, result$prediction, validation, held_side)
  list(rows=Filter(Negate(is.null), rows), spatial_k=result$spatial_k, training_radar_count=result$training_radar_count)
}

all_rows <- list()
for (held in continental_radars) {
  result <- run_fold(
    data[data$radar != held, , drop=FALSE],
    data[data$radar == held, , drop=FALSE],
    "leave_one_continental_radar_out", "continental"
  )
  all_rows <- c(all_rows, result$rows)
}
continent_to_uk <- run_fold(
  data[data$radar %in% continental_radars, , drop=FALSE],
  data[data$radar %in% uk_radars, , drop=FALSE],
  "train_continental_test_uk", "uk", TRUE
)
uk_to_continent <- run_fold(
  data[data$radar %in% uk_radars, , drop=FALSE],
  data[data$radar %in% continental_radars, , drop=FALSE],
  "train_uk_test_continental", "continental", TRUE
)
all_rows <- c(all_rows, continent_to_uk$rows, uk_to_continent$rows)

summary <- lapply(sort(unique(vapply(all_rows, function(row) row$validation, character(1)))), function(validation) {
  rows <- Filter(function(row) row$validation == validation, all_rows)
  scores <- vapply(rows, function(row) row$log1p_r_squared, numeric(1))
  f1 <- vapply(rows, function(row) row$top_decile_f1, numeric(1))
  list(
    validation=validation,
    radar_count=length(rows),
    median_log1p_r_squared=median(scores),
    positive_log1p_skill_fraction=mean(scores > 0),
    median_top_decile_f1=median(f1)
  )
})
jsonlite::write_json(list(
  schema_version="birdcast-euro-uk-coastal-corridor-mtr-1.0",
  model_id=spec$model_id,
  target=target,
  time_k=time_k,
  cohort_restriction=spec$cohort_restriction,
  evaluation_scope="geographic corridor spatial transfer; cross-network tests use the training source reference and are not site-calibrated",
  site_metrics=all_rows,
  summaries=summary
), args[[4]], auto_unbox=TRUE, pretty=TRUE)

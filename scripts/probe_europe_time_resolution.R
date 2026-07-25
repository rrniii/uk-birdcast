#!/usr/bin/env Rscript

# Test a candidate continuous UTC smooth resolution on the untouched Aloft cohort.
args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 4 || length(args) > 5) stop("usage: probe_europe_time_resolution.R SPEC.json TIME_K OUTPUT.json TARGET [SPACE_TIME_K]")
if (!requireNamespace("mgcv", quietly = TRUE) || !requireNamespace("jsonlite", quietly = TRUE)) stop("mgcv and jsonlite are required")
library(mgcv)
spec <- jsonlite::fromJSON(args[[1]], simplifyVector = TRUE)
time_k <- as.integer(args[[2]])
target <- args[[4]]
space_time_k <- if (length(args) == 5) as.integer(args[[5]]) else 0L
training <- utils::read.csv(spec$training_csv, check.names=FALSE)
transfer <- utils::read.csv(spec$validation_csv, check.names=FALSE)
transfer_radar_id <- as.character(transfer$radar)
origin <- min(as.POSIXct(training$time_utc, format="%Y-%m-%dT%H:%M:%OSZ", tz="UTC"))
prepare <- function(frame, levels_frame=NULL) {
  stamps <- as.POSIXct(frame$time_utc, format="%Y-%m-%dT%H:%M:%OSZ", tz="UTC")
  frame$time_index_hours <- as.numeric(difftime(stamps, origin, units="hours"))
  frame$utc_hour <- as.integer(format(stamps, "%H", tz="UTC"))
  frame$source <- factor(frame$source, levels=if (is.null(levels_frame)) unique(training$source) else levels(levels_frame$source))
  frame$country <- factor(frame$country, levels=if (is.null(levels_frame)) unique(training$country) else levels(levels_frame$country))
  frame$network <- factor(frame$network, levels=if (is.null(levels_frame)) unique(training$network) else levels(levels_frame$network))
  frame$radar <- factor(frame$radar, levels=if (is.null(levels_frame)) unique(training$radar) else levels(levels_frame$radar))
  frame
}
training <- prepare(training)
transfer <- prepare(transfer, training)
counts <- table(training$radar); training$w <- 1/as.numeric(counts[training$radar]); training$w <- training$w/mean(training$w)
complete <- complete.cases(training[,c("easting_m","northing_m",spec$predictors,"time_index_hours","utc_hour",target)])
training <- training[complete,]
training$response <- log1p(pmax(training[[target]],0))
terms <- c("s(easting_m,northing_m,bs='tp',k=40)", sprintf("s(%s,bs='tp',k=8)",spec$predictors), sprintf("s(time_index_hours,bs='cr',k=%d)",time_k), "s(utc_hour,bs='cc',k=12)")
if (space_time_k > 0) terms <- c(terms, sprintf("ti(easting_m,northing_m,time_index_hours,bs=c('tp','tp','cr'),k=c(8,8,%d))",space_time_k))
if (length(unique(training$source)) > 1) terms <- c("source", terms)
random_exclude <- character()
for (name in c("country", "network", "radar")) if (length(unique(training[[name]])) > 1) {
  terms <- c(terms, sprintf("s(%s,bs='re')",name))
  random_exclude <- c(random_exclude, sprintf("s(%s)",name))
}
formula <- as.formula(paste("response ~",paste(terms,collapse=" + ")))
# mgcv cannot discretise this nested tensor product.  Keep the efficient
# discretised path for ordinary time-smooth probes, but use the exact fREML
# implementation when evaluating a spatiotemporal interaction.
fit <- bam(
  formula, data=training, weights=w, method="fREML",
  discrete=space_time_k <= 0, nthreads=1
)
radar_id <- transfer_radar_id
transfer$source <- factor(spec$reference_source,levels=levels(training$source))
transfer$country <- factor(levels(training$country)[1],levels=levels(training$country))
transfer$network <- factor(levels(training$network)[1],levels=levels(training$network))
transfer$radar <- factor(levels(training$radar)[1],levels=levels(training$radar))
prediction <- pmax(expm1(predict(fit,newdata=transfer,exclude=random_exclude)),0)
rows <- lapply(sort(unique(radar_id)), function(radar) {
  i <- radar_id==radar & is.finite(transfer[[target]])
  o <- transfer[[target]][i]; p <- prediction[i]
  if (sum(i)<30) return(NULL)
  score <- function(observed, predicted) {
    log_r2 <- 1-sum((log1p(predicted)-log1p(observed))^2)/sum((log1p(observed)-mean(log1p(observed)))^2)
    oe <- observed>=quantile(observed,.9); pe <- predicted>=quantile(predicted,.9); tp <- sum(oe&pe); prec <- tp/sum(pe); rec <- tp/sum(oe)
    list(log1p_r_squared=log_r2,top_decile_f1=if(prec+rec>0)2*prec*rec/(prec+rec) else 0)
  }
  order <- order(transfer$time_index_hours[i])
  calibration_n <- max(30, floor(length(order)*.25))
  calibration <- order[seq_len(calibration_n)]
  evaluation <- order[(calibration_n+1):length(order)]
  offset <- mean(log1p(o[calibration])-log1p(p[calibration]))
  adjusted <- pmax(expm1(log1p(p[evaluation])+offset),0)
  list(radar=radar,row_count=sum(i),raw=score(o,p),calibration_row_count=calibration_n,
       calibration_log_offset=offset,post_calibration=score(o[evaluation],adjusted))
})
rows <- Filter(Negate(is.null),rows)
jsonlite::write_json(list(target=target,time_k=time_k,site_count=length(rows),
  space_time_k=space_time_k,
  raw_median_log1p_r_squared=median(sapply(rows,function(x)x$raw$log1p_r_squared)),
  raw_median_top_decile_f1=median(sapply(rows,function(x)x$raw$top_decile_f1)),
  calibrated_median_log1p_r_squared=median(sapply(rows,function(x)x$post_calibration$log1p_r_squared)),
  calibrated_median_top_decile_f1=median(sapply(rows,function(x)x$post_calibration$top_decile_f1)),
  calibration_policy="first chronological 25 percent of each held-out radar; fixed log offset; score remaining 75 percent",
  sites=rows),args[[3]],auto_unbox=TRUE,pretty=TRUE)

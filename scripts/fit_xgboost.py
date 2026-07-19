#!/usr/bin/env python3
"""Fit the no-time ERA5 XGBoost benchmark on JASMIN batch compute."""

from __future__ import annotations

import json
import math
from pathlib import Path
import sys


def score(observed, predicted):
    import numpy as np

    observed = np.asarray(observed, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    residual = predicted - observed
    threshold = float(np.quantile(observed, 0.9))
    predicted_event = predicted >= threshold
    observed_event = observed >= threshold
    return {
        "rmse": float(np.sqrt(np.mean(residual ** 2))),
        "mae": float(np.mean(np.abs(residual))),
        "bias": float(np.mean(residual)),
        "r_squared": float(1 - np.sum(residual ** 2) / np.sum((observed - observed.mean()) ** 2)) if np.var(observed) else 0.0,
        "top_decile_precision": float(np.mean(observed_event[predicted_event])) if predicted_event.any() else 0.0,
        "top_decile_recall": float(np.mean(predicted_event[observed_event])) if observed_event.any() else 0.0,
    }


def blocked_time_split(data):
    times = sorted(data.time_utc.astype(str).unique())
    if len(times) < 10:
        return None
    cutoff = times[max(0, int(len(times) * 0.8) - 1)]
    train = data.loc[data.time_utc.astype(str) <= cutoff]
    test = data.loc[data.time_utc.astype(str) > cutoff]
    if len(train) < 30 or not len(test):
        return None
    return train, test, cutoff


def main() -> int:
    if len(sys.argv) < 3:
        raise SystemExit("usage: fit_xgboost.py MODEL_SPEC.json OUTPUT_DIR [GRID.csv]")
    try:
        import numpy as np
        import pandas as pd
        import xgboost as xgb
    except ModuleNotFoundError as exc:
        raise SystemExit(f"missing JASMIN benchmark dependency: {exc.name}") from exc
    spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    output = Path(sys.argv[2]); output.mkdir(parents=True, exist_ok=True)
    grid = pd.read_csv(sys.argv[3]) if len(sys.argv) >= 4 else None
    frame = pd.read_csv(spec["training_csv"])
    predictors = [name for name in spec["predictors"] if name in frame.columns]
    targets = [*spec["intensity_targets"], *spec["vector_targets"]]
    metrics = []
    for pulse in spec["pulses"]:
        source = frame.loc[frame.pulse == pulse]
        for target in targets:
            required = ["radar", target, *predictors]
            data = source.dropna(subset=required).copy()
            if len(data) < 30 or data.radar.nunique() < 2:
                continue
            intensity = target in spec["intensity_targets"]
            y = np.cbrt(data[target].clip(lower=0).to_numpy()) if intensity else data[target].to_numpy()
            held_observed, held_predicted = [], []
            for radar in sorted(data.radar.unique()):
                train = data.radar != radar; test = ~train
                model = xgb.XGBRegressor(n_estimators=500, max_depth=5, learning_rate=.035, subsample=.8, colsample_bytree=.9, objective="reg:squarederror", n_jobs=1)
                model.fit(data.loc[train, predictors], y[train.to_numpy()], sample_weight=(data.loc[train, "profile_count"].clip(lower=1) if intensity else data.loc[train, "mtr_birds_km_h"].clip(lower=.01)))
                predicted = model.predict(data.loc[test, predictors]); predicted = np.maximum(predicted, 0) ** 3 if intensity else predicted
                held_observed.extend(data.loc[test, target]); held_predicted.extend(predicted)
            metrics.append({"pulse": pulse, "target": target, "validation": "leave_one_radar_out", "row_count": len(held_observed), **score(held_observed, held_predicted)})
            blocked = blocked_time_split(data)
            if blocked is not None:
                train, test, cutoff = blocked
                y_train = np.cbrt(train[target].clip(lower=0).to_numpy()) if intensity else train[target].to_numpy()
                time_model = xgb.XGBRegressor(n_estimators=500, max_depth=5, learning_rate=.035, subsample=.8, colsample_bytree=.9, objective="reg:squarederror", n_jobs=1)
                time_model.fit(train[predictors], y_train, sample_weight=(train["profile_count"].clip(lower=1) if intensity else train["mtr_birds_km_h"].clip(lower=.01)))
                time_predicted = time_model.predict(test[predictors])
                time_predicted = np.maximum(time_predicted, 0) ** 3 if intensity else time_predicted
                metrics.append({"pulse": pulse, "target": target, "validation": "blocked_time", "row_count": len(test), "cutoff_time_utc": cutoff, **score(test[target], time_predicted)})
            final = xgb.XGBRegressor(n_estimators=500, max_depth=5, learning_rate=.035, subsample=.8, colsample_bytree=.9, objective="reg:squarederror", n_jobs=1)
            final.fit(data[predictors], y, sample_weight=(data["profile_count"].clip(lower=1) if intensity else data["mtr_birds_km_h"].clip(lower=.01)))
            final.save_model(output / f"xgboost_{pulse}_{target}.json")
            if grid is not None:
                required_grid = ["time_utc", "longitude", "latitude", "support", *predictors]
                if not all(name in grid.columns for name in required_grid):
                    raise SystemExit("national ERA5 grid must include time_utc, coordinates, support, and all predictors")
                predicted = final.predict(grid[predictors]); predicted = np.maximum(predicted, 0) ** 3 if intensity else predicted
                prediction = grid[["time_utc", "longitude", "latitude", "support"]].copy()
                prediction["pulse"] = pulse; prediction["target"] = target; prediction["value"] = predicted; prediction["uncertainty"] = float("nan"); prediction["model_family"] = "xgboost"
                prediction.to_csv(output / f"prediction_{pulse}_{target}.csv", index=False)
    (output / "metrics.json").write_text(json.dumps({"model_family": "xgboost", "metrics": metrics, "model_time_terms": "none", "predictors": predictors}, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

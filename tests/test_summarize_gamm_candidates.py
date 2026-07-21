import importlib.util
import json
from pathlib import Path


def _load_module():
    path = Path(__file__).parents[1] / "scripts" / "summarize_gamm_candidates.py"
    spec = importlib.util.spec_from_file_location("summarize_gamm_candidates", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metric(pulse: str, target: str, r_squared: float, rmse: float) -> dict[str, object]:
    return {
        "pulse": pulse,
        "target": target,
        "validation": "leave_one_radar_out",
        "r_squared": r_squared,
        "rmse": rmse,
        "mae": rmse / 2,
        "bias": 0.0,
    }


def test_candidate_summary_requires_intensity_non_regression_and_lp_vector_gain(tmp_path: Path) -> None:
    module = _load_module()
    baseline_metrics = [
        _metric("lp", "mtr_birds_km_h", 0.23, 20.0),
        _metric("lp", "vid_birds_per_km2", 0.19, 0.55),
        _metric("sp", "mtr_birds_km_h", 0.20, 45.0),
        _metric("lp", "bird_u_ms", 0.06, 11.5),
        _metric("lp", "bird_v_ms", 0.04, 11.7),
    ]
    candidate_metrics = [
        _metric("lp", "mtr_birds_km_h", 0.23, 20.0),
        _metric("lp", "vid_birds_per_km2", 0.19, 0.55),
        _metric("sp", "mtr_birds_km_h", 0.20, 45.0),
        _metric("lp", "bird_u_ms", 0.10, 11.0),
        _metric("lp", "bird_v_ms", 0.07, 11.0),
    ]
    baseline = tmp_path / "baseline.json"
    candidate = tmp_path / "candidate.json"
    baseline.write_text(json.dumps({"metrics": baseline_metrics}), encoding="utf-8")
    candidate.write_text(json.dumps({"metrics": candidate_metrics}), encoding="utf-8")

    result = module.compare(module.read_loro_metrics(baseline), module.read_loro_metrics(candidate))

    assert result["selection_gate"]["intensity_non_regression"] is True
    assert result["selection_gate"]["lp_vector_gain"] is True
    assert result["selection_gate"]["eligible_for_follow_up"] is True

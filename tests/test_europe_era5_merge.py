from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    path = Path(__file__).parents[1] / "scripts" / "merge_europe_era5_site_features.py"
    spec = importlib.util.spec_from_file_location("merge_europe_era5_site_features", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_era5_site_merge_requires_complete_days(tmp_path: Path) -> None:
    root = tmp_path / "features"
    root.mkdir()
    path = root / "era5_site_features_20250714.json"
    single = {
        "radar": "bejab",
        "time_utc": "2025-07-14T00:00:00Z",
        "sp": 100000,
        "msl": 101000,
        "tcc": 0.5,
        "blh": 600,
        "tp": 0.001,
    }
    pressure = {
        "radar": "bejab",
        "time_utc": "2025-07-14T00:00:00Z",
        "t_pressure_level_850.0": 280,
        "r_pressure_level_850.0": 70,
        "u_pressure_level_850.0": 2,
        "v_pressure_level_850.0": 3,
        "u_pressure_level_925.0": 1,
        "v_pressure_level_925.0": 2,
        "u_pressure_level_700.0": 4,
        "v_pressure_level_700.0": 5,
    }
    path.write_text(json.dumps({"rows": [single, pressure]}), encoding="utf-8")
    path.with_suffix(".json.status.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    merge = _load().merge
    try:
        merge(root, tmp_path / "site.parquet", start_day="2025-07-14", end_day="2025-07-15")
    except ValueError as error:
        assert "missing Europe ERA5" in str(error)
    else:
        raise AssertionError("missing ERA5 day must block the join")

    second = root / "era5_site_features_20250715.json"
    second_single = {**single, "time_utc": "2025-07-15T00:00:00Z"}
    second_pressure = {**pressure, "time_utc": "2025-07-15T00:00:00Z"}
    second.write_text(json.dumps({"rows": [second_single, second_pressure]}), encoding="utf-8")
    second.with_suffix(".json.status.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    result = merge(root, tmp_path / "site.parquet", start_day="2025-07-14", end_day="2025-07-15")
    assert result["row_count"] == 2
    table = __import__("pyarrow.parquet", fromlist=["read_table"]).read_table(
        tmp_path / "site.parquet"
    )
    assert table.to_pylist()[0]["temperature_850_k"] == 280

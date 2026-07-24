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
    path.write_text(json.dumps({"rows": [{"radar": "bejab", "time_utc": "2025-07-14T00:00:00Z"}]}), encoding="utf-8")
    path.with_suffix(".json.status.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    merge = _load().merge
    try:
        merge(root, tmp_path / "site.parquet", start_day="2025-07-14", end_day="2025-07-15")
    except ValueError as error:
        assert "missing Europe ERA5" in str(error)
    else:
        raise AssertionError("missing ERA5 day must block the join")

    second = root / "era5_site_features_20250715.json"
    second.write_text(json.dumps({"rows": [{"radar": "bejab", "time_utc": "2025-07-15T00:00:00Z"}]}), encoding="utf-8")
    second.with_suffix(".json.status.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
    result = merge(root, tmp_path / "site.parquet", start_day="2025-07-14", end_day="2025-07-15")
    assert result["row_count"] == 2

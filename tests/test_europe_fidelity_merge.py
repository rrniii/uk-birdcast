from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load():
    path = Path(__file__).parents[1] / "scripts" / "merge_europe_fidelity.py"
    spec = importlib.util.spec_from_file_location("merge_europe_fidelity", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_fidelity_merge_requires_every_locked_chunk(tmp_path: Path) -> None:
    manifest = tmp_path / "chunks.jsonl"
    manifest.write_text('{"index":0}\n{"index":1}\n', encoding="utf-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "0.json").write_text(json.dumps({"status": "passed", "source_day_count": 2, "hourly_row_count": 3}), encoding="utf-8")
    merge = _load().merge
    try:
        merge(manifest, reports, tmp_path / "summary.json", release_id="release-a")
    except ValueError as error:
        assert "missing=1" in str(error)
    else:
        raise AssertionError("missing fidelity report must block the run")

    (reports / "0.json").write_text(json.dumps({"status": "passed", "release_id": "release-a", "source_day_count": 2, "hourly_row_count": 3}), encoding="utf-8")
    (reports / "1.json").write_text(json.dumps({"status": "passed", "release_id": "release-a", "source_day_count": 1, "hourly_row_count": 4}), encoding="utf-8")
    payload = merge(manifest, reports, tmp_path / "summary.json", release_id="release-a")
    assert payload["passed_chunk_count"] == 2
    assert payload["source_day_count"] == 3
    assert payload["release_id"] == "release-a"

    (reports / "1.json").write_text(json.dumps({"status": "passed", "release_id": "other-release"}), encoding="utf-8")
    try:
        merge(manifest, reports, tmp_path / "summary.json", release_id="release-a")
    except ValueError as error:
        assert "stale=1" in str(error)
    else:
        raise AssertionError("stale fidelity report must block the run")

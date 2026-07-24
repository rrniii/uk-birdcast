from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


def _load(name: str):
    path = Path(__file__).parents[1] / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace(".", "_"), path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_training_contract_freezes_common_predictors(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"predictors": ["u_850_ms", "surface_pressure_pa"]}), encoding="utf-8")
    table = tmp_path / "training.csv"
    table.write_text(
        "radar,time_utc,u_850_ms,surface_pressure_pa\nbejab,2025-07-14T00:00:00Z,2,100000\nbejab,2025-07-15T00:00:00Z,4,101000\n",
        encoding="utf-8",
    )
    payload = _load("build_europe_training_contract.py").build(table, spec, tmp_path / "contract.json")
    assert payload["first_day_utc"] == "2025-07-14"
    assert payload["feature_ranges"]["u_850_ms"]["upper"] == 4


def test_runtime_spec_requires_passed_fidelity(tmp_path: Path) -> None:
    spec = tmp_path / "spec.json"
    spec.write_text(json.dumps({"model_id": "test", "predictors": ["u_850_ms"]}), encoding="utf-8")
    training = tmp_path / "training.csv"
    validation = tmp_path / "validation.csv"
    training.write_text("x\n1\n", encoding="utf-8")
    validation.write_text("x\n1\n", encoding="utf-8")
    source = tmp_path / "source.json"
    fidelity = tmp_path / "training-fidelity.json"
    source.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    fidelity.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    output = tmp_path / "runtime.json"
    payload = _load("prepare_europe_model_spec.py").prepare(
        spec, training, validation, fidelity, source, output
    )
    assert payload["training_csv"] == str(training)
    assert payload["frozen_input_sha256"]["training_csv"]


def test_publication_denies_unpassed_model_validation(tmp_path: Path) -> None:
    module = _load("validate_europe_publication.py")
    source = tmp_path / "source.json"
    training = tmp_path / "training.json"
    model = tmp_path / "model.json"
    for path in (source, training):
        path.write_text(json.dumps({"status": "passed"}), encoding="utf-8")
    model.write_text(json.dumps({"release_passed": False}), encoding="utf-8")
    with pytest.raises(ValueError, match="model validation"):
        module.validate(tmp_path / "missing.csv", tmp_path, source, training, model, tmp_path / "out.json")

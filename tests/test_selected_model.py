from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import birdcast_uk.selected_model as selected_model


def _manifest(path: Path) -> dict[str, object]:
    return {
        "schema_version": selected_model.COMPONENT_MANIFEST_SCHEMA,
        "selection_id": selected_model.SELECTION_ID,
        "components": {
            pulse: {
                target: {
                    "model_rds": f"/private/{pulse}-{target}.rds",
                    "sha256": digest,
                }
                for target, digest in targets.items()
            }
            for pulse, targets in selected_model.COMPONENT_SHA256.items()
        },
    }


def test_exact_selected_component_contract_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "component-selection.json"
    path.write_text(json.dumps(_manifest(path)), encoding="utf-8")
    monkeypatch.setattr(
        selected_model,
        "COMPONENT_MANIFEST_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    payload = selected_model.validate_component_manifest(path)

    assert payload["selection_id"] == selected_model.SELECTION_ID
    assert len(selected_model.qualified_dates()) == selected_model.QUALIFIED_DAY_COUNT
    assert selected_model.qualified_dates()[0] == "2025-07-14"
    assert selected_model.qualified_dates()[-1] == "2026-07-13"
    provenance = selected_model.public_component_provenance(path)
    for pulse in selected_model.PULSES:
        for target in selected_model.TARGETS:
            component = provenance["components"][pulse][target]
            assert component["prediction_transform"] == selected_model.PREDICTION_TRANSFORM[target]
            assert component["uncertainty_scale"] == selected_model.UNCERTAINTY_SCALE


def test_selected_component_contract_rejects_an_unreviewed_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "component-selection.json"
    payload = _manifest(path)
    payload["components"]["sp"]["bird_u_ms"]["sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        selected_model,
        "COMPONENT_MANIFEST_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ValueError, match="component hash mismatch: sp/bird_u_ms"):
        selected_model.validate_component_manifest(path)


def test_selected_component_contract_rejects_a_conflicting_transform(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "component-selection.json"
    payload = _manifest(path)
    payload["components"]["lp"]["mtr_birds_km_h"]["prediction_transform"] = "identity"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        selected_model,
        "COMPONENT_MANIFEST_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )

    with pytest.raises(ValueError, match="transform mismatch: lp/mtr_birds_km_h"):
        selected_model.validate_component_manifest(path)


def test_operator_publication_config_matches_code_authority() -> None:
    path = Path(__file__).parents[1] / "configs" / "gamm_uk_holdout_component_publication.json"
    payload = json.loads(path.read_text(encoding="utf-8"))

    assert payload["selection_id"] == selected_model.SELECTION_ID
    assert payload["selection_manifest_sha256"] == selected_model.COMPONENT_MANIFEST_SHA256
    assert payload["component_sha256"] == selected_model.COMPONENT_SHA256
    assert payload["prediction_transform"] == selected_model.PREDICTION_TRANSFORM
    assert payload["uncertainty_scale"] == selected_model.UNCERTAINTY_SCALE
    assert payload["qualified_date_window"] == {
        "first_day": "2025-07-14",
        "last_day": "2026-07-13",
        "day_count": 365,
    }


def test_r_predictor_derives_the_locked_legacy_transform_contract() -> None:
    script = (Path(__file__).parents[1] / "scripts" / "predict_gamm_components.R").read_text(
        encoding="utf-8"
    )

    for target, transform in selected_model.PREDICTION_TRANSFORM.items():
        assert f'{target} = "{transform}"' in script
    assert "prediction_transform(component, target)" in script


def _write_grid(path: Path, *, fields: set[str] | None = None) -> None:
    columns = sorted(fields or selected_model.SELECTED_GRID_FIELDS)
    path.write_text(",".join(columns) + "\n" + ",".join("0" for _ in columns) + "\n")


def test_selected_grid_archive_requires_exact_dates_and_predictors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selected_model, "qualified_dates", lambda: ["2025-07-14", "2025-07-15"])
    _write_grid(tmp_path / "era5_grid_20250714.csv")
    _write_grid(tmp_path / "era5_grid_20250715.csv")

    paths = selected_model.validate_grid_archive(tmp_path)

    assert [path.name for path in paths] == [
        "era5_grid_20250714.csv",
        "era5_grid_20250715.csv",
    ]


def test_selected_grid_archive_rejects_missing_925_wind(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(selected_model, "qualified_dates", lambda: ["2025-07-14"])
    fields = set(selected_model.SELECTED_GRID_FIELDS) - {"u_925_ms", "v_925_ms"}
    _write_grid(tmp_path / "era5_grid_20250714.csv", fields=fields)

    with pytest.raises(ValueError, match="missing predictors.*u_925_ms.*v_925_ms"):
        selected_model.validate_grid_archive(tmp_path)

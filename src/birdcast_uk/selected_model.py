"""Immutable authority for the reviewed UK historical reanalysis model.

The release is intentionally narrower than a generic model registry: production
may publish only the eight components and 365-day evidence window reviewed on
22 July 2026. Research candidates stay outside the public artifact contract.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path
from typing import Any

SELECTION_ID = "uk-gamm-heldout-v2-sp-vector-925"
COMPONENT_MANIFEST_SCHEMA = "uk-gamm-component-selection-v1"
COMPONENT_MANIFEST_SHA256 = "fabfeceba85ed637b8eddd7909199e22913b251a901f5f452c8011a35e9ac3ea"
QUALIFIED_FIRST_DAY = date(2025, 7, 14)
QUALIFIED_LAST_DAY = date(2026, 7, 13)
QUALIFIED_DAY_COUNT = 365
PULSES = ("lp", "sp")
TARGETS = (
    "mtr_birds_km_h",
    "vid_birds_per_km2",
    "bird_u_ms",
    "bird_v_ms",
)
PREDICTION_TRANSFORM = {
    "mtr_birds_km_h": "square_nonnegative",
    "vid_birds_per_km2": "cube_nonnegative",
    "bird_u_ms": "identity",
    "bird_v_ms": "identity",
}
UNCERTAINTY_SCALE = "model_linear_predictor_standard_error"
COMPONENT_SHA256 = {
    "lp": {
        "mtr_birds_km_h": "06146a147b75a45339bfb149a693320ded42a9a10be37d3b45ce05720026b17d",
        "vid_birds_per_km2": "42ed30192cf018e48b707798c3068a7180982bef2c4518b599266e46f7cdfaeb",
        "bird_u_ms": "975d060f8b44fd00de9c0a59206453cd5c8cf081f0b7aee6f16005d73b75ab06",
        "bird_v_ms": "6ca5f4a3bb19542284731494028513686ac84657dfd34364d2e47b0ff137207d",
    },
    "sp": {
        "mtr_birds_km_h": "89c321633dad6ce51fd8565e41ed55aeb1246a36edd2684afe226dc857906e88",
        "vid_birds_per_km2": "37d8669dd09964953d5c3f1cc81305a557d171cb00454ec658f39f3bf4d738f3",
        "bird_u_ms": "c1586bf15891dc2bf2c357e4acfc864705c2338096725c7c62a7210afd0a44cb",
        "bird_v_ms": "6228cc628ff1904090ee392d02ebcf387ce9930e36a84898eabdbcabd23a3ab0",
    },
}
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def file_sha256(path: Path) -> str:
    """Return the lowercase SHA-256 of a file without loading it into memory."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def qualified_dates() -> list[str]:
    """Return the exact inclusive calendar window accepted for publication."""

    return [
        (QUALIFIED_FIRST_DAY + timedelta(days=offset)).isoformat()
        for offset in range(QUALIFIED_DAY_COUNT)
    ]


def validate_component_manifest(
    path: Path,
    *,
    verify_model_files: bool = False,
) -> dict[str, Any]:
    """Validate the exact reviewed manifest and, optionally, all model files."""

    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError(f"selected component manifest is missing: {path}")
    digest = file_sha256(path)
    if digest != COMPONENT_MANIFEST_SHA256:
        raise ValueError(
            "selected component manifest hash mismatch: "
            f"expected {COMPONENT_MANIFEST_SHA256}, got {digest}"
        )
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != COMPONENT_MANIFEST_SCHEMA:
        raise ValueError("selected component manifest schema mismatch")
    if payload.get("selection_id") != SELECTION_ID:
        raise ValueError("selected component manifest selection mismatch")
    components = payload.get("components")
    if not isinstance(components, dict) or set(components) != set(PULSES):
        raise ValueError("selected component manifest must contain exact LP and SP sets")
    for pulse in PULSES:
        pulse_components = components[pulse]
        if not isinstance(pulse_components, dict) or set(pulse_components) != set(TARGETS):
            raise ValueError(f"selected component manifest has an incomplete {pulse} set")
        for target in TARGETS:
            component = pulse_components[target]
            if not isinstance(component, dict):
                raise ValueError(f"selected component record is invalid: {pulse}/{target}")
            component_digest = str(component.get("sha256") or "").lower()
            if (
                SHA256_PATTERN.fullmatch(component_digest) is None
                or component_digest != COMPONENT_SHA256[pulse][target]
            ):
                raise ValueError(f"selected component hash mismatch: {pulse}/{target}")
            declared_transform = component.get("prediction_transform")
            if declared_transform not in (None, PREDICTION_TRANSFORM[target]):
                raise ValueError(f"selected component transform mismatch: {pulse}/{target}")
            declared_uncertainty = component.get("uncertainty_scale")
            if declared_uncertainty not in (None, UNCERTAINTY_SCALE):
                raise ValueError(f"selected component uncertainty scale mismatch: {pulse}/{target}")
            if verify_model_files:
                model = Path(str(component.get("model_rds") or ""))
                if not model.is_absolute():
                    model = path.parent / model
                if model.is_symlink() or not model.is_file():
                    raise FileNotFoundError(f"selected model file is missing: {model}")
                if file_sha256(model) != component_digest:
                    raise ValueError(f"selected model file hash mismatch: {pulse}/{target}")
    return payload


def public_component_provenance(path: Path) -> dict[str, object]:
    """Return the reviewed hashes without exposing private model paths."""

    validate_component_manifest(path)
    return {
        "component_manifest_sha256": COMPONENT_MANIFEST_SHA256,
        "components": {
            pulse: {
                target: {
                    "sha256": COMPONENT_SHA256[pulse][target],
                    # The reviewed manifest predates these descriptive fields.
                    # The values are derived from the locked fit contract and
                    # any later manifest may only repeat, never override, them.
                    "prediction_transform": PREDICTION_TRANSFORM[target],
                    "uncertainty_scale": UNCERTAINTY_SCALE,
                }
                for target in TARGETS
            }
            for pulse in PULSES
        },
    }

#!/usr/bin/env python3
"""Create the immutable Europe GAMM specification only after fidelity gates pass."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def _passed(path: Path, label: str) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "passed":
        raise ValueError(f"{label} did not pass")
    return payload


def prepare(
    model_spec: Path,
    training_csv: Path,
    validation_csv: Path,
    training_fidelity: Path,
    source_fidelity: Path,
    era5_fidelity: Path,
    output: Path,
) -> dict[str, object]:
    if not training_csv.is_file() or training_csv.stat().st_size == 0:
        raise ValueError("Europe training CSV is missing or empty")
    if not validation_csv.is_file() or validation_csv.stat().st_size == 0:
        raise ValueError("Europe transfer-validation CSV is missing or empty")
    _passed(training_fidelity, "Europe training fidelity")
    _passed(source_fidelity, "Europe source fidelity")
    _passed(era5_fidelity, "Europe ERA5 fidelity")
    payload = json.loads(model_spec.read_text(encoding="utf-8"))
    payload["training_csv"] = str(training_csv)
    payload["validation_csv"] = str(validation_csv)
    payload["training_fidelity"] = str(training_fidelity)
    payload["source_fidelity"] = str(source_fidelity)
    payload["era5_fidelity"] = str(era5_fidelity)
    payload["frozen_input_sha256"] = {
        "training_csv": hashlib.sha256(training_csv.read_bytes()).hexdigest(),
        "validation_csv": hashlib.sha256(validation_csv.read_bytes()).hexdigest(),
        "training_fidelity": hashlib.sha256(training_fidelity.read_bytes()).hexdigest(),
        "source_fidelity": hashlib.sha256(source_fidelity.read_bytes()).hexdigest(),
        "era5_fidelity": hashlib.sha256(era5_fidelity.read_bytes()).hexdigest(),
    }
    payload["runtime_spec_schema_version"] = "birdcast-euro-runtime-spec-1.0"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-spec", required=True)
    parser.add_argument("--training-csv", required=True)
    parser.add_argument("--validation-csv", required=True)
    parser.add_argument("--training-fidelity", required=True)
    parser.add_argument("--source-fidelity", required=True)
    parser.add_argument("--era5-fidelity", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(
        json.dumps(
            prepare(
                Path(args.model_spec),
                Path(args.training_csv),
                Path(args.validation_csv),
                Path(args.training_fidelity),
                Path(args.source_fidelity),
                Path(args.era5_fidelity),
                Path(args.output),
            ),
            indent=2,
        )
    )

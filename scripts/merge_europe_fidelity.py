#!/usr/bin/env python3
"""Fail closed unless every locked Europe source chunk passed reconstruction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def merge(manifest: Path, reports_root: Path, output: Path) -> dict[str, object]:
    chunks = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = {int(chunk["index"]): chunk for chunk in chunks}
    reports: dict[int, dict[str, object]] = {}
    for path in reports_root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            index = int(path.stem)
        except (ValueError, json.JSONDecodeError):
            continue
        reports[index] = payload
    missing = sorted(set(expected) - set(reports))
    unexpected = sorted(set(reports) - set(expected))
    failed = sorted(index for index, payload in reports.items() if payload.get("status") != "passed")
    if missing or unexpected or failed:
        raise ValueError(
            f"Europe source fidelity is incomplete: missing={len(missing)}, "
            f"unexpected={len(unexpected)}, failed={len(failed)}"
        )
    payload = {
        "schema_version": "birdcast-euro-fidelity-source-summary-1.0",
        "status": "passed",
        "raw_source_persisted": False,
        "expected_chunk_count": len(expected),
        "passed_chunk_count": len(reports),
        "source_day_count": sum(int(report.get("source_day_count") or 0) for report in reports.values()),
        "hourly_row_count": sum(int(report.get("hourly_row_count") or 0) for report in reports.values()),
        "manifest": str(manifest),
        "reports_root": str(reports_root),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--reports-root", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(merge(Path(args.manifest), Path(args.reports_root), Path(args.output)), indent=2))

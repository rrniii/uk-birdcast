#!/usr/bin/env python3
"""Fail closed unless every locked Europe source chunk passed reconstruction."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def merge(manifest: Path, reports_root: Path, output: Path, *, release_id: str) -> dict[str, object]:
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
    stale = sorted(index for index, payload in reports.items() if payload.get("release_id") != release_id)
    mismatched = sorted(
        index
        for index, payload in reports.items()
        if _chunk_identity(payload.get("chunk")) != _chunk_identity(expected.get(index))
    )
    if missing or unexpected or failed or stale or mismatched:
        raise ValueError(
            f"Europe source fidelity is incomplete: missing={len(missing)}, "
            f"unexpected={len(unexpected)}, failed={len(failed)}, stale={len(stale)}, "
            f"mismatched={len(mismatched)}"
        )
    payload = {
        "schema_version": "birdcast-euro-fidelity-source-summary-1.0",
        "status": "passed",
        "release_id": release_id,
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


def _chunk_identity(payload: object) -> tuple[str, str, str, str, str] | None:
    if not isinstance(payload, dict):
        return None
    required = ("source", "radar", "year", "month", "role")
    if any(key not in payload for key in required):
        return None
    return tuple(str(payload[key]) for key in required)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--reports-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(json.dumps(merge(Path(args.manifest), Path(args.reports_root), Path(args.output), release_id=args.release_id), indent=2))

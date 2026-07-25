#!/usr/bin/env python3
"""Fail closed unless every audited Europe grid day passed reconstruction."""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path


def merge(root: Path, output: Path, *, start_day: str, end_day: str, release_id: str) -> dict[str, object]:
    start = date.fromisoformat(start_day)
    end = date.fromisoformat(end_day)
    days = [start + timedelta(days=index) for index in range((end - start).days + 1)]
    reports = [root / f"{day:%Y%m%d}.json" for day in days]
    missing = [str(path) for path in reports if not path.is_file()]
    if missing:
        raise ValueError(f"Europe grid fidelity is incomplete: missing={len(missing)}")
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in reports]
    failed = [payload for payload in payloads if payload.get("status") != "passed" or payload.get("release_id") != release_id]
    if failed:
        raise ValueError(f"Europe grid fidelity has {len(failed)} failed or mismatched reports")
    counts = {int(payload["row_count"]) for payload in payloads}
    schemas = {tuple(payload["column_names"]) for payload in payloads}
    if len(counts) != 1 or len(schemas) != 1:
        raise ValueError("Europe grid fidelity reports disagree on row count or schema")
    result = {
        "schema_version": "birdcast-euro-grid-fidelity-summary-1.0",
        "status": "passed",
        "release_id": release_id,
        "expected_day_count": len(days),
        "passed_day_count": len(payloads),
        "daily_row_count": next(iter(counts)),
        "column_names": list(next(iter(schemas))),
        "reports_root": str(root),
        "raw_source_persisted": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-day", required=True)
    parser.add_argument("--end-day", required=True)
    parser.add_argument("--release-id", required=True)
    args = parser.parse_args()
    print(json.dumps(merge(Path(args.root), Path(args.output), start_day=args.start_day, end_day=args.end_day, release_id=args.release_id), indent=2))

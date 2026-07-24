#!/usr/bin/env python3
"""Fail closed unless every model-year Europe ERA5 reconstruction report passed."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
from pathlib import Path


def merge(reports_root: Path, output: Path, *, start_day: str, end_day: str) -> dict[str, object]:
    expected = [date.fromisoformat(start_day) + timedelta(days=index) for index in range(
        (date.fromisoformat(end_day) - date.fromisoformat(start_day)).days + 1
    )]
    reports = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in reports_root.glob("*.json")}
    expected_stamps = {day.strftime("%Y%m%d") for day in expected}
    missing = sorted(expected_stamps - set(reports))
    unexpected = sorted(set(reports) - expected_stamps)
    failed = sorted(stamp for stamp, payload in reports.items() if payload.get("status") != "passed")
    if missing or unexpected or failed:
        raise ValueError(
            f"Europe ERA5 fidelity is incomplete: missing={len(missing)}, unexpected={len(unexpected)}, failed={len(failed)}"
        )
    payload = {
        "schema_version": "birdcast-euro-era5-fidelity-summary-1.0",
        "status": "passed",
        "expected_day_count": len(expected),
        "passed_day_count": len(reports),
        "site_feature_row_count": sum(int(report.get("row_count") or 0) for report in reports.values()),
        "reports_root": str(reports_root),
        "raw_source_persisted": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reports-root", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start-day", required=True)
    parser.add_argument("--end-day", required=True)
    args = parser.parse_args()
    print(json.dumps(merge(Path(args.reports_root), Path(args.output), start_day=args.start_day, end_day=args.end_day), indent=2))

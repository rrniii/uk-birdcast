#!/usr/bin/env python3
"""Install only the named Bird Maps cron block, with a recoverable prior table."""

import argparse
import hashlib
import json
import os
import shlex
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BEGIN = "# BEGIN UK BIRD MAPS HISTORICAL PUBLICATION"
END = "# END UK BIRD MAPS HISTORICAL PUBLICATION"


def updated_table(
    previous: str, submitter: Path, environment: Path, product: str = "historical"
) -> str:
    if product not in {"historical", "model"}:
        raise ValueError("Unsupported publication cycle")
    begin = BEGIN if product == "historical" else "# BEGIN UK BIRD MAPS MODEL PUBLICATION"
    end_marker = END if product == "historical" else "# END UK BIRD MAPS MODEL PUBLICATION"
    schedule = "35 */6 * * *" if product == "historical" else "20 2 * * *"
    if previous.count(begin) != previous.count(end_marker) or previous.count(begin) > 1:
        raise ValueError("Existing Bird Maps cron markers are ambiguous")
    if begin in previous:
        start, end = previous.index(begin), previous.index(end_marker) + len(end_marker)
        if start > end:
            raise ValueError("Existing Bird Maps cron markers are reversed")
        previous = previous[:start] + previous[end:].lstrip("\n")
    command = shlex.join(["/bin/bash", str(submitter), str(environment)])
    if f"submit-{product}-cycle.sh" in previous:
        raise ValueError(f"An unmanaged {product}-cycle entry already exists")
    return (
        previous.rstrip("\n")
        + "\n\n"
        + "\n".join(
            [
                begin,
                "CRON_TZ=UTC",
                # Cron has a smaller PATH than an interactive JASMIN shell.
                f"{schedule} /usr/local/bin/crontamer -t 5m -l {shlex.quote(command)}",
                end_marker,
                "",
            ]
        )
    )


def read_table() -> str:
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode and "no crontab" not in result.stderr.lower():
        raise RuntimeError(result.stderr.strip())
    return result.stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submitter", type=Path, required=True)
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--product", choices=("historical", "model"), default="historical")
    args = parser.parse_args()
    if socket.getfqdn() != "cron-01.jasmin.ac.uk":
        raise SystemExit("This installer must run on cron-01.jasmin.ac.uk")
    for path in (args.submitter, args.environment):
        if not path.is_absolute() or not path.is_file() or "\n" in str(path) or "%" in str(path):
            raise SystemExit(f"Invalid installed path: {path}")
    previous = read_table()
    updated = updated_table(previous, args.submitter, args.environment, args.product)
    if previous == updated:
        print(f"Bird Maps {args.product} cron entry already matches")
        return
    os.umask(0o077)
    args.backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    backup = args.backup_dir / f"crontab-{stamp}.before"
    backup.write_text(previous)
    digest = hashlib.sha256(backup.read_bytes()).hexdigest()
    if digest != hashlib.sha256(previous.encode()).hexdigest():
        raise RuntimeError("Crontab recovery copy failed verification")
    if read_table() != previous:
        raise RuntimeError("Crontab changed concurrently; no replacement installed")
    subprocess.run(["crontab", "-"], input=updated, text=True, check=True)
    if read_table() != updated:
        raise RuntimeError("Installed crontab does not match the requested table")
    print(
        json.dumps(
            {
                "installed": True,
                "backup": str(backup),
                "backup_sha256": digest,
                "schedule": "00:35, 06:35, 12:35, 18:35 UTC daily"
                if args.product == "historical"
                else "02:20 UTC daily",
            }
        )
    )


if __name__ == "__main__":
    main()

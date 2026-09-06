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


def updated_table(previous: str, submitter: Path, environment: Path) -> str:
    if previous.count(BEGIN) != previous.count(END) or previous.count(BEGIN) > 1:
        raise ValueError("Existing Bird Maps cron markers are ambiguous")
    if BEGIN in previous:
        start, end = previous.index(BEGIN), previous.index(END) + len(END)
        if start > end:
            raise ValueError("Existing Bird Maps cron markers are reversed")
        previous = previous[:start] + previous[end:].lstrip("\n")
    command = shlex.join(["/bin/bash", str(submitter), str(environment)])
    if "submit-historical-cycle.sh" in previous:
        raise ValueError("An unmanaged historical-cycle entry already exists")
    return (
        previous.rstrip("\n")
        + "\n\n"
        + "\n".join(
            [
                BEGIN,
                "CRON_TZ=UTC",
                f"35 */6 * * * crontamer -t 5m -l {shlex.quote(command)}",
                END,
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
    args = parser.parse_args()
    if socket.getfqdn() != "cron-01.jasmin.ac.uk":
        raise SystemExit("This installer must run on cron-01.jasmin.ac.uk")
    for path in (args.submitter, args.environment):
        if not path.is_absolute() or not path.is_file() or "\n" in str(path) or "%" in str(path):
            raise SystemExit(f"Invalid installed path: {path}")
    previous = read_table()
    updated = updated_table(previous, args.submitter, args.environment)
    if previous == updated:
        print("Bird Maps six-hourly cron entry already matches")
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
                "schedule": "00:35, 06:35, 12:35, 18:35 UTC daily",
            }
        )
    )


if __name__ == "__main__":
    main()

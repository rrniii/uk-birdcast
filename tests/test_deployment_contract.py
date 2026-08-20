from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def _run(*command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def test_immutable_release_accepts_its_venv_and_rejects_untracked_source(
    tmp_path: Path,
) -> None:
    """A normal venv links to a base interpreter but retains its own sys.prefix."""

    release = tmp_path / "release"
    package = release / "src" / "birdcast_uk"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("__version__ = 'test'\n", encoding="utf-8")
    (release / ".gitignore").write_text(".venv/\n__pycache__/\n", encoding="utf-8")
    _run("git", "init", cwd=release)
    _run("git", "config", "user.name", "BirdCast test", cwd=release)
    _run("git", "config", "user.email", "birdcast-test@example.invalid", cwd=release)
    _run("git", "add", ".", cwd=release)
    _run("git", "commit", "-m", "test release", cwd=release)
    sha = _run("git", "rev-parse", "HEAD", cwd=release).stdout.strip()

    subprocess.run(
        [sys.executable, "-m", "venv", str(release / ".venv")],
        check=True,
        capture_output=True,
        text=True,
    )
    script = Path(__file__).parents[1] / "deploy" / "scripts" / "verify-immutable-release.sh"
    env = {
        **os.environ,
        "BIRDCAST_UK_ROOT": str(release),
        "BIRDCAST_UK_RELEASE_SHA": sha,
        "BIRDCAST_UK_PYTHON": str(release / ".venv" / "bin" / "python"),
    }

    accepted = subprocess.run(
        ["sh", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert accepted.returncode == 0, accepted.stderr

    (release / "unexpected.py").write_text("raise SystemExit\n", encoding="utf-8")
    rejected = subprocess.run(
        ["sh", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    assert rejected.returncode != 0

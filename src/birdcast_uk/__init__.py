"""Research-first UK BirdCast static data pipeline."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # pyproject.toml is the single version source for installed distributions.
    __version__ = version("birdcast-uk")
except PackageNotFoundError:
    __version__ = "0+unknown"

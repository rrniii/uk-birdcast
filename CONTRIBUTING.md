# Contributing

`main` is the only long-lived branch and the only branch eligible for release.
Use a short-lived topic branch, open a pull request into `main`, and remove the
topic branch after merge. Europe, forecast research, and operational work all
live in the same repository history; do not maintain parallel product branches.

Before requesting review, run:

```bash
python -m pip install -e ".[birdcast,dev]"
ruff check .
ruff format --check .
pytest -q
find . -type f \( -name '*.sh' -o -name '*.sbatch' \) \
  -not -path './.git/*' -exec bash -n {} \;
```

Scientific-contract changes require a regression test and an update to the
relevant documentation. Never weaken a completeness, provenance, validation,
or publication gate merely to make a release pass.

Production deployment requires all CI checks, review, and a clean commit on
`main`. Record and deploy the exact commit SHA. Do not deploy an uncommitted
working tree, a mutable branch tip, or a research refit in place of the selected
component manifest.

Forecast code is retained for research, but forecast and ECMWF services must
remain disabled until the documented latency and per-radar freshness contract
is implemented and independently accepted.

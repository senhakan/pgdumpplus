# Export statistics benchmark inputs

`scenarios.example.json` is a safe, data-free scenario template for
`scripts/benchmark_stats.py`. Copy it to a private experiment directory and
add only synthetic-table selection, filter and mask arguments appropriate for
the disposable fixture. Do not commit database names, endpoints, credentials,
raw dumps, stderr logs or benchmark results from private infrastructure.

The runner writes `manifest.json`, `runs.csv`, `summary.json`, archive files,
stderr and restore logs under one explicit output directory. A successful
local run on a short fixture is a functional runner check, not a performance
claim. The S3 experiment requires the pinned reference/candidate builds, a
frozen synthetic fixture, seven or more balanced A/B/C triplets per scenario,
and successful restore validation on the designated benchmark host.

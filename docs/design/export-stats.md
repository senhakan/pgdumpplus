# Export-time table statistics: implementation and decision plan

Status: IN PROGRESS. S1/S2 implementation and cross-version verification are
complete; the required host-scale timing experiment and owner continuation
decision remain before release integration.
Owner-facing requirement and decision report language: Turkish.
Reviewed: 2026-09-21; repository baseline: `5369a348ad95d39c81839569892e3890850f9f31`.
Manifest at review: project 2.1.1, PostgreSQL 13–18, existing x86_64 targets.
This document supersedes earlier conversational proposals for pre-counts,
estimated statistics, source relation size and multiple statistics options.
It takes precedence over the older default roadmap for this feature only.

## 1. Requirement and scope

One argument, `--stats`, prints actual exported rows and uncompressed exported
data bytes for each completed table on stderr. It works without `-v`; adding
`-v` preserves ordinary verbose messages and adds the same completion lines once.
Default export behavior and output remain unchanged when the flag is absent.
No COUNT, EXPLAIN, reltuples, relation-size query, extra connection, pre-scan,
payload reparse, or second execution of filters/masks is permitted in production.

Proposed example (not available in the released binary):

```bash
pg_dumpplus --stats -v -Fc -Z 5 -U postgres -d example -f example.dump 2>example.log
```

```text
pg_dumpplus: table "public"."orders": rows=125438, bytes=88080384 (84.00 MiB uncompressed), elapsed=2.400 s
```

`rows` has no locale grouping; `bytes` is an exact unsigned decimal integer;
the parenthesized binary unit is rounded for humans. Escape control characters
in identifiers so each completion remains one physical log line. Never print
row values, filter literals, mask expressions or connection credentials.
No percent-complete, live row updates, ETA, JSON/CSV CLI, stats file option or
cross-worker aggregate is required in version one. Logs use ordinary redirection.

The exported-byte measure is the serialized table data before archive compression:
COPY data buffers only (including their field separators and record terminators),
excluding COPY headers/end marker, archive framing, metadata and indexes. INSERT
mode measures emitted INSERT statements including SQL syntax/separators; document
that COPY and INSERT byte totals therefore have different representations.
Empty COPY tables have zero rows and zero payload bytes. This is neither heap size
nor table-specific compressed file size. Record complete archive size separately
in benchmarks. Do not label a custom-format archive as binary COPY: its table
payload follows the existing text COPY path.

Per-table completion means data extraction/writing and that table's archive-end
callback succeeded. It is not a promise that the entire archive was closed,
fsynced or will restore successfully. The process exit code and restore tests
remain authoritative; a later table or final-close failure invalidates the dump.

## 2. Feasibility and source evidence

The local patched PG17.11 source was inspected, including `dumpTableData_copy`,
`dumpTableData_insert`, `WriteData` and `WriteDataChunksForTocEntry`. Local PG13.23
source was located as a compatibility reference. No new binary was built or tested
for this review. All six supported majors still require implementation verification.

- COPY receives buffers through `PQgetCopyData` and passes their known length to
  `WriteData`. Length addition requires no rescan or extra SQL.
- After COPY ends, the code already calls `PQgetResult`, checks COMMAND_OK and
  clears the result. Read `PQcmdTuples` at this point before clearing it. PostgreSQL
  documents COPY support for this API; verify real COPY and COPY(SELECT) results
  on all supported majors before relying on it.
- INSERT uses a cursor and FETCH batches and writes tuples through archputs/
  archprintf. Count emitted tuples, including the zero-column DEFAULT VALUES path,
  not INSERT statements or newline characters.
- `WriteDataChunksForTocEntry` invokes the data callback and then the format's
  end callback. Logging inside COPY alone would occur before archive finalization
  for that entry. Find and cover the plain-output equivalent as well.

References: [PostgreSQL result APIs](https://www.postgresql.org/docs/17/libpq-exec.html),
[COPY APIs](https://www.postgresql.org/docs/17/libpq-copy.html),
[PG17 dump source](https://github.com/postgres/postgres/blob/REL_17_11/src/bin/pg_dump/pg_dump.c),
[PG17 archiver source](https://github.com/postgres/postgres/blob/REL_17_11/src/bin/pg_dump/pg_backup_archiver.c).

Conclusion: feasible without pre-count queries; expected overhead is small but
unmeasured. No numeric performance claim is justified before the experiments below.

## 3. Implementation contract

### CLI and per-table lifecycle

Add a no-argument option and help text through `scripts/apply_pgdumpplus.py`.
Choose an option ID after checking all supported upstream sources and existing
custom options. Reject `--stats=value`. Repeating the boolean flag is harmless.
`--stats --dry-run` fails before connecting/exporting; dry-run does not extract data.
Schema-only/sections without table data produce no stats lines and otherwise retain
upstream behavior. Data-only, table selection and exclusions report only entries
whose data is actually exported. Preserve existing invalid-option errors.

State belongs to the archive/worker and current TABLE DATA entry, not a shared
global accumulator. Suggested fields: enabled, active, row_count, payload_bytes,
monotonic_start, count_valid, data_complete. Use checked 64-bit arithmetic and
strict numeric parsing. Invalid/missing COPY counts must never become a fake zero;
make this an explicit diagnostic and unsuccessful stats-enabled run if runtime
evidence shows no supported valid fallback. Do not add a hidden COUNT fallback.

Start timing immediately before the table data callback. End after the relevant
format end callback succeeds. Document that this excludes earlier catalog/lock
work and final whole-archive close/fsync. Read the clock only at table boundaries.
Publish once, then reset state; errors/cancellation publish no completion for the
unfinished table. Counters must not allocate per row or retain table contents.

### Counting and byte accounting

COPY: prefer the existing final command's `PQcmdTuples` over a new per-row counter.
Validate and store it after COMMAND_OK and before PQclear. Accumulate positive
buffer lengths only after the corresponding write call succeeds. Do not scan
newlines, call strlen on payloads, or assume a network packet equals one row.
Capture stats only on the ordinary existing data path; filters and masks naturally
affect the measured output without reevaluating expressions.

INSERT: count each emitted tuple, covering multi-row INSERT, DEFAULT VALUES,
generated columns and the final partial batch. Count serialized statement bytes
at an existing output boundary with known lengths. Scope accounting so headers,
unrelated archive entries and COPY end markers cannot leak into totals. A shared
WriteData hook is acceptable only if active scope and all archputs/archprintf
paths are proven; otherwise use narrow path-specific hooks. No new strlen pass.
ON CONFLICT DO NOTHING still reports exported tuples, not rows eventually inserted
by a restore into a populated target.

Parallel directory export: verify option/state propagation to every worker and
table reset. Use the existing synchronized logging mechanism if it guarantees
complete lines; otherwise implement bounded complete-line delivery suitable for
supported platforms. Do not assume separate fprintf calls are atomic. Stress-test
long/quoted identifiers and simultaneous short-table completions. Completion order
is nondeterministic. Avoid new polling or serialized data-writing bottlenecks.

Report the source TABLE DATA entry for partitions, including load-via-partition-root;
do not add parent totals or double count leaves. Inheritance/filter query semantics
remain as currently implemented; inspect for pre-existing duplication and report
any discovered defect separately rather than silently modifying SQL for stats.
Sequences, blobs and materialized-view refresh commands are not table-row exports;
exclude them from per-table statistics. Included foreign-table data follows its
real COPY/INSERT path and needs a small owned FDW fixture if supported by the suite.

### Files and compatibility

Expected edits: patcher; `scripts/verify_isolated.py`; focused patcher tests;
new `scripts/benchmark_stats.py`; synthetic fixture/report templates under
`docs/benchmarks/`; README and Turkish guides after owner continuation decision.
Generated upstream C changes may require pg_dump.c, pg_backup.h,
pg_backup_archiver.h/c and their existing logging facilities. Inspect each source
before adding a patch target, and preserve patcher atomicity/idempotence.
Do not check generated source trees or binaries into Git.
Always regenerate from pristine manifest-pinned source after revising a patch.
Compiled runtime gains no Python dependency, extension or server configuration.

## 4. Functional acceptance

Use only runner-owned synthetic databases and disposable runtime containers on
the owner's designated test server. Obtain connection details from private local
configuration, never tracked files. No production database operations are needed.

Required tests compare stats to independent expectations and restored contents:

| Case | Required assertion |
| --- | --- |
| Known 0, 1 and many rows | Exact rows; empty COPY bytes=0; one completion per entry |
| Fixed UTF-8/tab/newline/backslash/NULL/bytea data | COPY serialized-byte oracle matches; escaped content cannot change row count |
| WHERE and equivalent profile, plus masks | Restored selected/masked values and counts agree; expressions execute once |
| Plain, custom Z0/Z5, tar, directory serial/parallel | Same COPY rows/bytes; valid archive and error-free restore |
| INSERT/column INSERT/rows-per-insert/default-values | Tuple counts and exact emitted statement bytes agree |
| Partitions/inheritance/root-load and excluded tables | Source-entry identity; no invented parent aggregates or extra lines |
| Identifier controls/quotes/Unicode and many short tables | Single safe lines; no mixed worker output or duplicates |
| Schema-only/dry-run/invalid combinations/stats without verbose | CLI contract; stdout remains dump-only |
| Query failure, disconnect, write failure and cancellation | Nonzero process result; no completed line for unfinished entry |
| Late archive-close failure | Failure preserved even after earlier completed-table lines |
| Concurrent writes in owned fixture | Existing snapshot consistency preserved |
| 64-bit arithmetic | Values above 32-bit limits via focused helper tests without enormous fixtures |

Byte oracle must be independent (known serialized fixtures or independently
extracted table payload), not a second call to the new counter. Compare restored
values and constraints, not just pg_restore --list. COUNT in the verification
harness after timed export/restore is allowed; it is forbidden in the feature.
Do not require raw archive hashes to match: timestamps, random metadata and worker
ordering can differ. Compare logical results and normalize only known metadata.
Verify absence of added SQL with isolated-server query logging or captured query
traces outside timed runs; never enable logging on the shared existing server.

## 5. Performance experiment: mandatory before continuation decision

### Baselines

Build R from the unchanged pinned repository revision and C from the candidate,
using identical upstream source, compiler, flags, libraries and packaging layout.
Do not compare an old package to a differently optimized candidate alone.

| Label | Invocation | Purpose |
| --- | --- | --- |
| A | R with -v, no --stats | Normal pre-change export |
| B | C with -v, no --stats | Detect regression with feature disabled |
| C | Same C binary with -v --stats | Isolate enabling stats, including log I/O |
| U | Matching vanilla pg_dump with -v | Context only on unfiltered/unmasked cases |

Measure B/A, C/B and C/A. Do not compare filtered C against full A and call it
stats overhead. Include a small without-verbose C check to verify independent CLI
behavior; headline comparison uses the owner's -v -Fc -Z 5 workflow.

### Workloads and procedure

Primary performance environments: disposable Rocky 9 and 10 runtime containers
on the designated test host, matching PG17 client/server, then endpoint-major
PG13/18 performance spot checks. All PG13–18 majors receive functional coverage;
existing Ubuntu/EL8 and other manifest targets retain normal build/package gates.
Do not claim performance coverage for every package based on these measurements.

Fixture generator is deterministic with fixed seed/date cutoff, table definitions,
expected row counts and a manifest of sizes. Include: many narrow rows (counter
overhead); wide/TOAST values; compressible and low-compressibility payloads; 1,000
small/empty tables (logging overhead); multi-table dataset for parallel export.
Calibrate a main synthetic dataset to take at least 30 seconds per normal custom
dump where resources allow. Record actual size/rows, not an assumed duration.
If too short, report limited measurement resolution and scale within free space.

Mandatory primary scenarios: full custom -Z5; same with selective date filter;
same with profile filter plus mask; custom -Z0; plain COPY; directory -Z5 -j4;
many-small-table custom; INSERT fixture of manageable size. Run identical arguments,
credentials, destination filesystem and fixture snapshot for each A/B/C triplet.
Use a frozen fixture with no writers. Preparation, ANALYZE, restore and validation
are outside export timing. Do not benchmark parallel scenarios concurrently with
other benchmark runs or builds; -j4 itself remains part of the measured command.

Record host/container CPU/memory limits, storage, free space, client/server identities,
compiler flags, compression libraries, dataset seed and load observations privately.
Use one untimed warm-up per variant/scenario and at least seven timed triplets in
balanced rotating order (ABC, BCA, CAB...). These are warm-cache experiments; never
drop host caches or restart the shared PostgreSQL service. Optional cold-cache
experiments require an isolated environment and separate reporting.

Time process start through successful exit, including close/fsync and stderr file
writes, using a monotonic timer. Capture wall seconds, client user/system CPU,
peak RSS, exit code, archive disk bytes and stderr bytes for every run. Record
server/host load separately if obtainable without reconfiguration. Keep each log
and artifact under a unique owned run directory; check capacity before each run.
No /dev/null-only headline test. Retention/cleanup must target explicit owned paths;
never recursively clear shared temporary directories. Use bounded iteration/artifact
retention if space is limited, recording hashes and verification before cleanup.

Report each raw run, median time and IQR/range, paired relative and absolute deltas:
`overhead_pct = 100 * (T_enabled - T_disabled) / T_disabled`.
Use paired bootstrap 95% confidence intervals with recorded seed for C/B and B/A;
if noise obscures the proposed threshold, run one further seven-triplet batch,
then label inconclusive if still noisy. Do not discard slow runs without an explicit
recorded external cause; retain them and show sensitivity analysis.
Restore at least one successful artifact per variant/scenario and validate counts/
values; all timed runs must exit successfully and be readable by matching tools.

### Proposed interpretation (recommendation, not automatic authorization)

- Target: C/B median overhead <=2% for primary custom -Z5, with upper confidence
  bound <=5%; B/A median <=1%, upper bound <=3%.
- Investigate: primary C/B >2%, disabled regression >1%, or inconclusive confidence
  bounds. Include CPU/RSS and many-small-table logging effects, not just wall time.
- Recommend optimization before continuation: reproducible C/B >5% on a primary
  workload, any correctness failure, material memory growth or disabled regression.
- Report every other scenario and absolute seconds, including regressions hidden
  by compression. Thresholds are proposed review criteria, not measured results or
  an owner-approved performance budget. Passing never bypasses the owner decision.

## 6. Report and owner decision

Produce Turkish `report.md` and self-contained `report.html` plus `runs.csv`,
`manifest.json`, commands, build logs, per-run stderr and restore-verification
results in a private owned experiment directory. No remote HTML dependencies.
Generate MD/HTML from the same result dataset. A sanitized report may be tracked
after review; private endpoints, data and credentials must never enter Git.

Report must show: checked revisions/binary hashes; implemented behavior example;
byte definition; actual test coverage; per-scenario A/B/C medians, differences in
seconds/percent and confidence bounds; CPU/RSS; failures/limitations; restore
evidence; and the implementer's continue/optimize/defer recommendation.
Do not write placeholders that look like real measurements.

Mandatory stop: after correctness evidence and performance report are ready,
present them to the owner and ask for the continuation decision. No merge into
the release branch, version bump, release tag, publication, installer switch or
replacement of installed clients before that decision. A positive benchmark is
not approval. Record the owner's decision and exact candidate revision in TASKS.md.
If changes affect runtime after this review, rerun affected comparisons; material
performance changes require presenting updated evidence before publication.

## 7. Tasks, dependencies and handoff

| ID | Work | Acceptance / next boundary |
| --- | --- | --- |
| S0 | This design/review | Consistent linked plan and queue; no runtime changes |
| S1 | Authorized prototype: CLI, COPY rows/bytes, lifecycle/logging | PG17 compiled proof, exact oracle, failed-write check; no pre-count |
| S2 | INSERT, parallel, edge cases and major compatibility | Section 4 passes PG13–18; unchanged disabled behavior |
| S3 | Reproducible benchmark runner and experiment | Raw A/B/C data, required scenarios and valid restores |
| S4 | Turkish MD/HTML report and owner review | Evidence delivered; stop for continue/optimize/defer decision |
| S5 | After positive decision: finalize docs and package verification | Actual manifest build/runtime gates including Rocky 9/10; docs accurate |
| S6 | Release under existing release authorization/gates | Immutable artifacts, verified downloads; no test substitutions |

Dependencies: S1 requires implementation authorization; S2 follows S1; S3 follows
S2 (runner preparation may accompany S1/S2); S4 follows S3; S5 requires explicit
S4 decision; S6 follows S5 and applicable release authority. This planning request
completes only S0. A subsequent instruction to implement this plan authorizes S1
through S4, not bypassing the explicitly requested S4 continuation decision.

Each handoff records task/status, Git revision and diff, generated upstream
versions, reproducible commands, test outcomes, private artifact location (only in
private notes), sanitized evidence links, known problems and exact next action.
If unable to test the designated host, finish safe local preparation and report
the missing measurements; do not silently substitute another host's performance.

Planning verification: git diff --check, linked-file existence, consistency with
AGENTS/PLAN/TASKS and manifest. Runtime builds are not required for this doc change.

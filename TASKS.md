# Execution tracker

Updated: 2026-09-22. Specification: [PLAN.md](PLAN.md).
Statuses: READY, IN_PROGRESS, BLOCKED (dependency stated), DONE (evidence required).

## Current request: export-time statistics planning

Specification: [export-stats.md](docs/design/export-stats.md).
Local review baseline `5369a348ad95d39c81839569892e3890850f9f31`, manifest 2.2.0.
Historical release claims below are evidence ledger entries, not current state.

Installer maintenance evidence (2026-09-22): on RPM-based hosts where PGDG
installs PostgreSQL under `/usr/pgsql-<major>/bin`, root's `PATH` may not expose
`psql`. Commit `b39d376` updates the installer to inspect standard versioned
binary paths, query the `postgres` service account when available, and retain
the package-manager fallback. `bash -n`, public documentation checks and
`git diff --check` pass. The fix is scheduled for the v2.2.1 full release
matrix; no private host or credential is included.

| ID | Status | Dependency / required result |
| --- | --- | --- |
| S0 | DONE | Design/source review and linked execution plan; documentation checks recorded below |
| S1 | DONE | COPY rows/bytes prototype and PG13–18/OS matrix evidence complete; INSERT/parallel remain S2 |
| S2 | DONE | INSERT/parallel/PG13–18 correctness and restored-value evidence |
| S3 | DONE | Rocky 9 + PostgreSQL 17 A/B/C benchmark and restore evidence complete |
| S4 | DONE | Owner approved continuation after timing report |
| S5 | DONE | Stats display integration, docs and package verification |
| S6 | DONE | v2.2.0 matrix, TAP, provenance and release verification complete |

S0 evidence (2026-09-21): inspected local patched PG17.11 COPY/INSERT/archiver paths
and official libpq result documentation; `git diff --check` and local document-link
checks pass. No stats implementation, benchmark, server mutation or publication
performed. Performance expectations are unmeasured. Exact next step: when the user
authorizes implementation, start S1 using the linked design; proceed through S4
and present actual timing/restore evidence before asking for continuation.

S1 implementation evidence (2026-09-21, working tree): added `--stats` to the
compiled client. COPY rows come from the completed COPY command's `PQcmdTuples()`;
bytes are the successful `WriteData` buffer lengths. No COUNT/EXPLAIN/extra SQL or
payload scan is used. Patcher idempotence and atomicity checks pass for pristine
PG17.11 and PG13.23 sources. PG17.11 generated C compiled successfully locally.
A disposable Docker PostgreSQL 17.5 fixture reported `stats_empty` 0/0,
`stats_filtered` 10/106, `stats_small` 7/56, and a filtered export reported 5/56;
custom archive restore returned 7 rows. `--stats` alone emits the completion line
without `-v`. The isolated suite now includes an independent COPY row/byte oracle;
the full suite still needs a matching PG17 client environment after this change.
The first full matrix exposed a PostgreSQL 13-only logging API mismatch; commit
`4d7b36a` adds the PG13 `pg_logging_set_level` compatibility branch. A fresh
pristine PG13.23 source now patches and compiles `pg_dumpplus`/`pg_restore`
successfully. An explicit unwritable-output test returns non-zero and reports
`Permission denied`. Focused post-fix CI [35574551592](https://github.com/senhakan/pgdumpplus/actions/runs/35574551592)
passed plan, PG17.11 EL9 build, Ubuntu 24.04 DEB build, isolated PG17 verify,
DEB/RPM smoke and Rocky 9 smoke. Full candidate matrix
[35575004840](https://github.com/senhakan/pgdumpplus/actions/runs/35575004840) passed
all PG13.23–18.6 verify/build/package/smoke jobs across EL8/9/10 and
Ubuntu 22.04/24.04/Debian 12; its dispatch-only attestation verification is
separate from the completed build/test jobs. S1 is complete. Exact next step:
start S2 for INSERT, parallel and cross-major runtime coverage; no release or
benchmark claim is made by S1.

CI profile update (2026-09-21): `.github/workflows/build.yml` now uses PG17 +
EL9 + Ubuntu 24.04 for normal pushes and manual development runs. Version tags
select the full PG13–18, EL8/9/10 and Ubuntu/Debian matrix; `full_matrix=true` is
the manual candidate-preparation switch. Full-matrix evidence is recorded in
run 35575004840 above; the dispatch-only release-attestation step does not
publish a release and is not part of the S1 runtime/package test claim.

S2 implementation evidence (2026-09-21, working tree): extended the archive
statistics state into `pg_backup.h` and the archiver so INSERT output counts
tuples and measures bytes at existing `archputs`/`archprintf` write boundaries.
Completion is reported after the format end callback; the plain SQL archive
path uses the same reporting helper, and parallel directory workers emit
complete bounded lines. COPY now stores a validated 64-bit `PQcmdTuples()`
count and defers its report to the same lifecycle boundary. The isolated
fixture adds INSERT/column-insert/rows-per-insert/default-values assertions and
an independent SQL-byte oracle. Local PG17.11 COPY, INSERT, column INSERT,
parallel directory and restore checks pass; pristine PG13.23 and PG17.11
patcher/idempotence checks pass and both generated clients compile. S2 remains
IN_PROGRESS until the post-change isolated CI and full PG13–18 matrix pass.
The first full-matrix attempt [35579585876](https://github.com/senhakan/pgdumpplus/actions/runs/35579585876)
exposed a patcher anchor difference in PG15/16 `CreateArchive` signatures;
version-shape-specific anchors now pass local PG13/15/16/17 patcher checks and
the full matrix will be rerun.

S2 completion evidence (2026-09-21): focused CI [35579250167](https://github.com/senhakan/pgdumpplus/actions/runs/35579250167)
passed the expanded isolated suite (52 cases), PG17 build/package/restore and
Rocky 9 smoke. Full matrix [35580451097](https://github.com/senhakan/pgdumpplus/actions/runs/35580451097)
passed all PG13.23–18.6 build, verify, package and EL8/9/10 plus Ubuntu/Debian
smoke jobs; the dispatch release-attestation step also completed successfully.
The suite now covers COPY and INSERT/column-insert SQL byte oracles, tuple
counts, rows-per-insert, zero-dumpable-column DEFAULT VALUES, custom/plain/
directory-parallel restores and disabled-stat behavior. S2 is DONE.

S3 preparation (2026-09-21): added `scripts/benchmark_stats.py` and the
data-free scenario template under `docs/benchmarks/`. The runner records
reference/candidate hashes, raw A/B/C wall/CPU/RSS/archive/stderr data, paired
bootstrap intervals and optional disposable restore validation. Timed variants
now rotate ABC/BCA/CAB across iterations and the template uses `-v` for the
headline workflow. A local Docker functional smoke produced 63 raw runs with
the rotated order and a summary, but is not a performance claim.
The user narrowed the decision experiment to Rocky Linux 9 + PostgreSQL 17.
That scope was executed in a disposable Rocky 9 container against a synthetic
PostgreSQL 17 fixture; no private endpoint, credential or real dataset was used.

S3 completion evidence (2026-09-21): a Rocky 9 RPM was built from pristine
PostgreSQL 17.11 sources with patcher atomicity/idempotence checks. The runner
completed 21 warm-ups, 63 balanced A/B/C timed runs and 63 disposable restores;
all validation queries returned 250000 rows. Median C/B deltas were -1.15%
(custom -Z5), +13.18% (plain COPY) and +3.31% (directory -j4 -Z5). These are
warm-cache, local synthetic measurements rather than a general performance
claim. Turkish Markdown and HTML decision reports were generated in a private
run directory and are intentionally not tracked. S3 and S4 are DONE; S5 is
in progress for display integration, docs and package verification.

Focused CI evidence: [35585515209](https://github.com/senhakan/pgdumpplus/actions/runs/35585515209)
completed successfully after the benchmark evidence update. It ran exactly
PG17.11 verify, EL9 RPM build/smoke, Ubuntu 24.04 DEB build/smoke and Rocky
Linux 9 package execution. This run was a development validation; no release
was published.

Final decision experiment evidence (2026-09-21): on the owner's Rocky Linux
9.5/PostgreSQL 17.8 test server, the same PG17.11 vanilla/candidate clients ran
7 A/B/C repetitions for 1M, 10M and 100M synthetic rows using `-v -Fc -Z5`.
Median C/B deltas were -0.04%, -3.40% and -0.15%; archive sizes were identical
between stats-disabled and stats-enabled candidates. A same-row payload probe
and a 1,000-table probe were also run (63 and 21 runs): no monotonic row/byte
runtime penalty was observed, while stats adds approximately 55 bytes of stderr
per completed table. Nine representative archives passed `pg_restore --list`.
The private Turkish MD/HTML report records raw evidence and limits; no raw
archives, logs, endpoint, credentials or real data are tracked. The owner
approved continuation on 2026-09-21; S4 is DONE.

Local Docker follow-up evidence (2026-09-21): on Ubuntu with an isolated
PostgreSQL 17.5 container, a fresh synthetic 1M/10M/100M fixture was measured
with the same PG17.11 vanilla/candidate clients. A cache-balanced run completed
63/63 A/B/C exports (7 rotated iterations per size); archives were hashed and
discarded after each run to avoid filling the host disk. Median C/B deltas were
-4.44% (1M), +2.78% (10M) and -9.38% (100M); archive sizes were identical and
stats added about 186–394 bytes of stderr per table. This is a local
steady-state/cache-balanced measurement, not a cache-cold claim; global page
cache eviction was intentionally not used to avoid affecting unrelated
services. Raw output remains outside the repository at the handoff.

Stats display follow-up (2026-09-21): the completion line now reports
`rows`, human-readable binary `size` (`B`/`KB`/`MB`/`GB`/`TB`) and monotonic
human-readable `duration` (`ms`, `s`, `m s`, or `h m s`). A fresh PG17.11 build
passed the isolated Docker suite (53/53), including COPY/INSERT/column-insert,
filters, masks, parallel directory output and restores. A 1M synthetic export
produced `rows=1000000, size=38.04 MB, duration=2s`. Non-data objects (schemas,
sequences, indexes and metadata) intentionally produce no table statistics;
only completed table-data entries do.

S5 completion evidence (2026-09-21): PG17.11 pristine patcher/build produced
the 2.2.0 Ubuntu package; package extraction smoke showed the new help text and
version. Public audit, Python syntax, diff and release-gate checks passed.
The isolated PG17 suite and 1M output evidence above passed before packaging.

S6 completion evidence (2026-09-21): commit `f27f83f` was tagged `v2.2.0`.
Full release CI [35626290007](https://github.com/senhakan/pgdumpplus/actions/runs/35626290007)
completed 98/98 jobs successfully across PG13–18, EL8/9/10, Ubuntu/Debian,
verification, smoke, SBOM and provenance checks. Upstream TAP
[35626289411](https://github.com/senhakan/pgdumpplus/actions/runs/35626289411)
also passed. The stable [v2.2.0 release](https://github.com/senhakan/pgdumpplus/releases/tag/v2.2.0)
is published with packages, tarballs, checksums, SBOM and installer.

## Verified baseline

- Canonical repo senhakan/pgdumpplus; command pg_dumpplus.
- Main baseline f3285c3; latest stable release v1.2.0 and verified candidate
  v2.0.0-rc.1 are published.
- [CI 35253870996](https://github.com/senhakan/pgdumpplus/actions/runs/35253870996): success.
- Patcher safety, compiled packages, PG13/17 roundtrips and package smoke tests exist.
- New presets are on main after v1.2.0; not yet part of a newer stable release.
- Strict masks, catalog-only dry-run and compiled profiles are implemented and
  covered by CI. Deterministic pseudonyms remain unimplemented pending C2 review.
- This tracker supersedes stale defect/completion claims in the previous list.

## Work queue

| ID | Status | Dependencies | Deliverable (PLAN section) |
| --- | --- | --- | --- |
| A1 | DONE | — | Strict masks and regression coverage (A1); local PG17.5 and CI PG13/17 checks pass |
| A2 | DONE | A1 | Catalog-only dry-run and JSON schema (A2) |
| A3 | DONE | A1 | Type/format/partition/snapshot/upstream coverage (A3) |
| B1 | DONE | — | Version manifest, supported bases, package identity/order and cross-major guard (B1) |
| B2 | DONE | — | Source hashes, CI permissions, SBOM/provenance (B2) |
| B3 | DONE | A1, A2, A3, B1, B2, D3a | Verified v2.0 candidate/stable promotion (B3) |
| C1 | DONE | A2 | Compiled profile reader and tested examples (C1); strict v1 parser and full matrix evidence complete |
| C2-design | IN_PROGRESS | A3 | Key/type/execution design and review in `docs/design/pseudonymization.md` (C2-design) |
| C2 | BLOCKED | C1, C2-design | Typed deterministic pseudonyms (C2) |
| D1-linux | BLOCKED | B1, B2 | Native Linux ARM64 packages (D1) |
| D1-macos | BLOCKED | D1-linux | macOS packages and Homebrew tap (D1) |
| D2-local | BLOCKED | B1, B2 | Signed APT/RPM metadata and local client tests (D2) |
| D2-public | BLOCKED | D2-local, B3, hosting/key decision | Hosted channels and upgrades (D2) |
| D3a | DONE | — | Accurate README/TR, matrix, changelog, contribution/security docs (D3) |
| D3b | DONE | B3 | Release-binary demo/tutorials and launch drafts (D3) |
| CLEAN1 | DONE | — | Audit and retire unsafe/redundant legacy entry points |

CLEAN1: completed. Destructive and superseded shell scripts were removed after
their useful coverage was consolidated in `verify_isolated.py`; no repository
references remain.

## Release boundaries

1. v2.0: A1–A3, B1–B3, D3a. Strict masking is a breaking behavior change.
2. Later minor releases: C1, then C2 after architecture and runtime evidence.
3. D1/D2 each ship only after their own platform/channel gates pass.
4. D3b demonstrates an actual release, not unreleased functionality.

These are target milestones, not published versions or calendar promises.

## Evidence ledger

| Task | Commit/run | Observed result | Limits |
| --- | --- | --- | --- |
| Baseline | f3285c3 / 35253870996 | CI success | Existing matrix only |
| Planning | Working tree, 2026-09-17 | Plan and agent handoff created | No implementation or release performed |
| A1 local | a80188d | PG17.5 candidate build + isolated suite: 40 passed; patcher/idempotence checks passed | Strict validation is fail-fast |
| A1 CI | [35260089452](https://github.com/senhakan/pgdumpplus/actions/runs/35260089452) | PG13/17 builds, isolated verification and DEB/RPM smoke jobs passed | Release publication remains a separate B3 gate |
| A2 CI | [35264310934](https://github.com/senhakan/pgdumpplus/actions/runs/35264310934) | Dry-run text/JSON checks plus PG13/17 builds and package smoke jobs passed | Superseded by newer coverage below |
| A2 escaping CI | [35266116755](https://github.com/senhakan/pgdumpplus/actions/runs/35266116755) | JSON identifier escaping test, PG13/17 builds and package smoke jobs passed | Superseded by latest full matrix |
| Current CI | [35267465415](https://github.com/senhakan/pgdumpplus/actions/runs/35267465415) | All PG13/17 build, verify, DEB and RPM smoke jobs passed | Release-only attestation runs on a version tag |
| CLEAN1 | 1b79a32 + local audit | Retired destructive/redundant legacy scripts; consolidated verification remains in `verify_isolated.py` | Historical private test environments are intentionally not reproduced |
| Latest CI | [35269564040](https://github.com/senhakan/pgdumpplus/actions/runs/35269564040) | Commit dd8b81e: PG13/17 builds, isolated verification, DEB/RPM smoke jobs all passed | Release publication and provenance remain tag-only B3 gates |
| D3a | 035371e | Public README, Turkish guide, contribution guidance, security policy and issue templates reviewed; keyword stuffing and unqualified privacy claims removed | Release-binary tutorials and launch drafts wait for B3 |
| B1 source refresh | local + support-matrix.json | PostgreSQL 13.23 (legacy), 17.11 and 18.6 source hashes recorded; PG18.6 patcher clean/idempotence checks passed | Matrix result is recorded in the current-matrix row |
| B1 current matrix | [35270331753](https://github.com/senhakan/pgdumpplus/actions/runs/35270331753) | Commit 30f45cf: PG13.23/17.11/18.6 builds, isolated dump/restore, all DEB/RPM smoke jobs passed | Project-version-only upgrade ordering and release promotion remain B1/B3 work |
| B2 action pinning | 4fcdeae | All third-party workflow actions are pinned to reviewed immutable commit SHAs; Dependabot remains configured for update proposals | Release attestation itself is exercised only by a real candidate tag |
| A2 final | [35270987562](https://github.com/senhakan/pgdumpplus/actions/runs/35270987562) | Commit fc754c3: dry-run text/JSON, identifier escaping, custom-SQL non-execution and option error checks passed across PG13.23/17.11/18.6; all package smoke jobs passed | Custom expressions are reported, not fully type/runtime evaluated in dry-run |
| B1 package metadata fix | 25c1669 + local PG18.6 build | PG18’s changed schema-only option representation is handled conditionally; local DEB metadata reports project 1.2.0 and upstream 18.6 separately | Full matrix result is recorded below |
| Latest compatibility CI | [35272617138](https://github.com/senhakan/pgdumpplus/actions/runs/35272617138) | Commit 42b8543: PG13.23/17.11/18.6 builds, verify jobs, all DEB/RPM smoke jobs passed after PG18 fix | Release job skipped because no tag was created |
| Latest docs CI | [35273077003](https://github.com/senhakan/pgdumpplus/actions/runs/35273077003) | Commit 54f26f8: current PG13.23/17.11/18.6 build, verify and package smoke matrix passed | Release job skipped because no tag was created |
| Latest CI | [35273364265](https://github.com/senhakan/pgdumpplus/actions/runs/35273364265) | Commit ec35514: immutable-action workflow, PG13.23/17.11/18.6 build, verify and all DEB/RPM smoke jobs passed | Release job skipped because no tag was created |
| A3 domain/Unicode CI | [35278380212](https://github.com/senhakan/pgdumpplus/actions/runs/35278380212) | Commit 27b285e: text-domain custom cast, Unicode value, strict domain preset rejection and full PG13.23/17.11/18.6 matrix passed | Broader snapshot-concurrency and upstream regression evidence remains |
| A3 snapshot CI | [35279332809](https://github.com/senhakan/pgdumpplus/actions/runs/35279332809) | Commit 839f8b5: concurrent committed update during delayed export restored as one consistent snapshot across PG13.23/17.11/18.6 | Full upstream regression suite remains outside client-only build; cross-format snapshot tests continue |
| Latest CI | [35278650592](https://github.com/senhakan/pgdumpplus/actions/runs/35278650592) | Commit 3df6272: documentation evidence update plus full PG13.23/17.11/18.6 build, verify and package smoke matrix passed | Release publication remains blocked by open A3/B1 gates |
| B2 provenance dispatch | [35275197819](https://github.com/senhakan/pgdumpplus/actions/runs/35275197819) | Tagless dispatch passed full matrix, SBOM generation, tampered-file checksum rejection, asset attestations and `gh attestation verify` for every asset | No release was published; candidate/stable promotion remains B3 |
| A3 format/upstream comparison | [35280543186](https://github.com/senhakan/pgdumpplus/actions/runs/35280543186) | Commit b59f6e1: vanilla-vs-pgdumpplus plain and schema-only output comparison, plus concurrent snapshot checks for plain, custom and directory formats; PG13.23/17.11/18.6 matrix and package smoke tests passed | Upstream vanilla TAP coverage is recorded in the dedicated matrix run below |
| A3 upstream TAP | [35295293808](https://github.com/senhakan/pgdumpplus/actions/runs/35295293808) | Commit a13f623: pristine PostgreSQL 13.23, 17.11 and 18.6 sources built with TAP enabled; `src/bin/pg_dump` upstream TAP suites all reported success | TAP covers upstream vanilla behavior; pg_dumpplus-specific differences remain covered by the isolated matrix above |
| B1 build identity | [35281659213](https://github.com/senhakan/pgdumpplus/actions/runs/35281659213) | Commit 23d9027: `--build-info` reports project SemVer, upstream PostgreSQL version and source commit; runtime-only DEB/RPM smoke tests and PG13.23/17.11/18.6 matrix passed | Upgrade ordering is covered by the later B1 package-upgrade row |
| B1 package upgrade ordering | [35283866146](https://github.com/senhakan/pgdumpplus/actions/runs/35283866146) | Commit ded0ded: DEB metadata fixture and separately built lower-project-version RPM fixture upgraded to the current package in runtime-only containers; install, version change, build identity, binary checks and removal passed across the verified matrix | DEB fixture keeps current binary identity because it tests package-manager ordering only; a release candidate still requires B3 gates |
| B1 cross-major guard | [35286844662](https://github.com/senhakan/pgdumpplus/actions/runs/35286844662) | Commit c4891d5: verify workflow connects the PG17 client to an isolated PG18.6 service and confirms PostgreSQL’s real `server version mismatch` refusal before export; full matrix passed | The guard is supplied by the matching upstream client path; v2 publication remains governed by the B3 gate |
| Public audit | [35287648751](https://github.com/senhakan/pgdumpplus/actions/runs/35287648751) | Commit 1f36ee9: tracked-file audit rejects credential, private-key and private-network markers; local and CI checks passed | Pattern scan complements, but does not replace, GitHub secret scanning |
| C1 profile examples | 02fcd2c / looped `python3 -m json.tool` | Tenant-subset, support-extract and full-redaction v1 examples parse as valid JSON and are referenced from the public README | Runtime equivalence is covered by the compiled-profile CI case below |
| C1 validator hardening | 689c2e5 / local validator checks | CI now exercises rejection of duplicate keys and unknown fields in addition to validating all three examples | Superseded by the full CI evidence below |
| C1 validator CI | [35290657033](https://github.com/senhakan/pgdumpplus/actions/runs/35290657033) | Commit 64a2c1c: profile examples, duplicate-key, unknown-field, duplicate-mask-target and non-integer schema-version rejection passed in the full build/package/verify matrix | Runtime parser evidence is recorded in the C1 full-matrix row |
| C1 compiled reader | local PG17.11 build / `verify_isolated.py` case | Added dependency-free compiled `--profile=FILE` parser with 256 KiB, UTF-8, depth, duplicate/unknown-field and exactly-one preset/expression checks; profile rules feed the same CLI model and local binary rejected invalid schema and resolved a valid example before connection | Full matrix result is recorded below |
| C1 full matrix | [35299099839](https://github.com/senhakan/pgdumpplus/actions/runs/35299099839) | Commit 40d5c21: compiled profile examples resolved through catalog-only JSON plans and plain export/restore; profile filter reduced orders to 25 and masks passed, invalid schema was rejected; PG13.23/17.11/18.6 build, verify and DEB/RPM smoke matrix passed | Profiles remain trusted SQL input; no includes or environment expansion are supported |
| B3 release gate | [35295641589](https://github.com/senhakan/pgdumpplus/actions/runs/35295641589) | Commit 389ee28: full CI passed with v2 negative/positive gate tests; the real v2 gate now passes after A3 completion | Candidate/stable promotion still requires the documented release procedure and independent asset verification |
| B3 candidate release | [35296823454](https://github.com/senhakan/pgdumpplus/actions/runs/35296823454) | Tag `v2.0.0-rc.1` passed the full build, isolated verification, DEB/RPM smoke, TAP and provenance pipeline; [candidate release](https://github.com/senhakan/pgdumpplus/releases/tag/v2.0.0-rc.1) is published as a prerelease with 36 platform packages, SBOM and SHA256 manifest | Stable promotion remains a separate decision after candidate review |
| D3b launch assets | [35300111335](https://github.com/senhakan/pgdumpplus/actions/runs/35300111335) | Commit c3d0037: added release-binary tenant, date-range and masked-customer tutorials plus a non-published launch draft; documentation links and full matrix CI passed | Tutorials use synthetic/approved data; external posting remains manual |
| D2 distribution contract | [35292021026](https://github.com/senhakan/pgdumpplus/actions/runs/35292021026) | Commit 6420190: signed-channel contract documentation and links passed the full build/package/verify matrix | Hosted repository, signing-key ownership and real channel install/upgrade tests remain external release decisions |
| C2 design contract | [35292795924](https://github.com/senhakan/pgdumpplus/actions/runs/35292795924) | Commit de4a70f: typed scope, canonicalization, protected key-FD interface and leakage-test requirements documented and passed the full CI matrix | Security review and implementation evidence are still required before marking C2-design done |
| C2 review checklist | local / `docs/design/pseudonymization-review.md` | Added an explicit primitive, key-boundary, leakage, typed-output and parallelism gate matrix; it deliberately keeps C2 blocked until independent review and runtime vectors exist | No pseudonymization code or CLI is enabled |

Add exact checked commit, CI URL or command and actual outcome for each task.
Never infer a test count or mark an ongoing run passed. Keep private evidence
outside Git; put only sanitized conclusions here.

## Resume instructions

For the current statistics request, follow S0–S6 above. Implementation and
continuation are authorized; complete S5 and apply the release gates before S6.
The C2 roadmap below remains separate and is not the next task for this request.

Next implementation task: complete the security review of
`docs/design/pseudonymization.md` (C2-design), then implement typed
pseudonymization only after its key-handling blockers are resolved. C1’s JSON
validator remains build-time drift protection in addition to the compiled
client parser.

D1 and D2 remain gated on native platform/channel evidence. D2-public also
needs an owner for hosting and signing keys. At interruption record active task,
changed paths, last check, exact next action and unresolved blocker. PLAN.md
contains the required behavior contract.

# pg_dumpplus implementation and release plan

Baseline: 2026-09-17, commit f3285c3.
Canonical repository: https://github.com/senhakan/pgdumpplus
This is a specification for future work, not a claim that the features exist.

## Current feature planning addendum (2026-09-21)

The export-time `--stats` specification is in
[docs/design/export-stats.md](docs/design/export-stats.md). It defines actual
exported rows/bytes, a single flag, implementation tasks S0–S6 and mandatory
normal-export versus stats-enabled timing experiments. Implementation was
authorized and S1/S2 are now verified; the owner must decide whether to continue
after the experiment before release integration/publication. Earlier
pre-count/source-size proposals are superseded. For this feature, use the
S-task queue rather than the historical default task below. Historical
baseline/release statements below are dated; the manifest currently records
project 2.2.0, not the original v1.2.0 baseline.

### CI execution profiles

Pushes to `main` and ordinary development runs verify only the PG17 tested minor,
the EL9 RPM build used by the test environment, and the Ubuntu 24.04 DEB/runtime
path. This keeps feedback short without claiming that unbuilt majors or platforms
are covered by that run. A version tag selects the complete manifest matrix:
PG13–18, EL8/9/10 and Ubuntu/Debian targets. The release job consumes only those
full-matrix artifacts. Manual dispatch keeps the development profile by default;
`full_matrix=true` selects the requested full-matrix versions for candidate
preparation. A normal development push never silently broadens its matrix.

## Product contract

A separately installed compiled PostgreSQL dump client with row filtering and
explicit column masking. Client installation requires no compiler, Python,
PostgreSQL server or server extension. Python is a build dependency only.
Preserve system PostgreSQL tools, supported formats and snapshot consistency.

Primary uses: tenant subsets, support extracts and selected staging data.
Partial masking is not anonymization or a legal compliance guarantee.
Filters do not automatically preserve foreign-key relationships.
GUI, hosted service, telemetry and automatic PII discovery are out of scope.

## Baseline and implementation map

Latest published release at inspection: v1.2.0. New presets are on main after
that tag, not in a newer stable release. Main CI for f3285c3 succeeded:
https://github.com/senhakan/pgdumpplus/actions/runs/35253870996

Current verified build matrix: PostgreSQL 13.23/17.11/18.6, Linux x86_64, EL8,
Ubuntu 22.04/24.04, Debian 12. Patcher atomicity/idempotence, integration and
native install/upgrade/removal checks exist. Invalid mask columns still warn
and continue; built-in text masks can be incompatible with destination types.

| Area | Entry point |
| --- | --- |
| C generation, CLI, mask resolution/validation, COPY/INSERT | scripts/apply_pgdumpplus.py; MASK_RESOLVE_C, MASK_VALIDATE_C |
| Disposable database roundtrips | scripts/verify_isolated.py |
| Atomic patch application | scripts/test_patcher.py |
| Packaging | scripts/package_client.py, scripts/build.sh, scripts/build_deb.sh |
| Install/upgrade/remove | scripts/smoke_package.sh |
| CI and releases | .github/workflows/build.yml, .github/workflows/verify.yml |
| Usage | README.md, docs/pgdumplus-tr.md |
| Legacy entry points | Retired; verification coverage is consolidated in `scripts/verify_isolated.py` |

## Execution protocol for any model

1. Read AGENTS.md, PLAN.md and TASKS.md. Inspect Git status/diffs and preserve user edits.
2. Check actual release and CI state. A successful main build is not a release.
3. Select the first READY task with completed dependencies; mark IN_PROGRESS.
4. Add regression coverage for behavioral defects, implement and run required checks.
   Apply each revised patch to pristine upstream sources.
5. Record checked commit, CI URL or reproducible commands, result and limitations.
   Mark DONE only after every acceptance criterion passes.
6. At handoff record the active task, changed files, last check, unresolved failure,
   exact next action and external blockers. No prior conversation is required.
7. Planning alone authorizes no feature implementation, release or provisioning.

Use synthetic data in uniquely named databases created by the test invocation.
Never change existing databases, server settings or installed clients for testing.
Run package smoke checks in disposable containers. Keep secrets, internal hosts,
real data and private reports out of Git, logs and assets. Never restore cleaned
old history. Public docs describe product use, not private test infrastructure.

## A — Reliable export behavior

### A1: Strict masking (first implementation task)

Default to fatal error when a requested mask cannot be applied. No silent fallback
to original data. Validate all resolved rules before table-data export, including
parallel workers.

Required behavior:
- Unknown table/column, generated/dropped column and masks targeting excluded
  tables fail. Every wildcard match must pass validation.
- Duplicate masks resolving to the same table/column fail. Repeated filters on a
  table combine with AND; inspect existing composition and document migration.
- Mask plus schema-only output fails with an actionable diagnostic. Data-only
  output validates rules normally.
- Built-in text presets accept confirmed text-compatible types. Use catalog
  type resolution, including domains, not display-name string matching.
  Reject native UUID/numeric targets for star-producing text presets.
- Custom SQL remains available. Syntax/type preflight is best effort; runtime
  expressions, CHECK, FK and uniqueness cannot all be guaranteed in advance.
- Diagnostics identify the rule/object without exposing row data or secrets.
  Nonzero exit means discard the dump. Headers or incomplete files can exist;
  do not promise zero output bytes or a usable partial backup.
- Do not initially add an ignore-errors switch.

Files: patcher validation/resolution and integration suite.
Acceptance: negative cases above fail; an identifiable sensitive fixture marker
does not appear in output for invalid rules; COPY, INSERT, column INSERT, custom
and parallel directory formats pass; ordinary unmasked output matches upstream.
Replace the existing skipped-mask success test with strict failure assertions.
Document the breaking change and before/after migration examples.

### A2: Catalog-only dry run

Proposed options: --dry-run and --plan-format=text|json. Reject plan-format alone
and reject -f with dry-run. Share resolution/validation with actual dump.
No data extraction, arbitrary custom SQL execution, row counts or dump file creation.

Report resolved objects, column types, preset or custom-expression indicator,
filter presence and issues. Never print filter literals, raw custom expressions,
connection secrets or sampled values. JSON schema_version is 1; deterministic
ordering; stdout contains only the plan; diagnostics use stderr.
Exit 0 for valid plans, nonzero on errors. Include structured validation errors
when possible. Explain that custom SQL has not been fully evaluated.

Acceptance: dry-run and real export resolve identical rules; invalid rules fail
both; a side-effecting custom SQL expression is never executed in dry-run;
no table-data query or dump artifact is produced. Document the JSON schema.

### A3: Edge cases and upstream compatibility

Extend roundtrip coverage: NULL, empty/short strings, Unicode, whitespace,
malformed emails, formatted IBAN/card strings, tc and card_number aliases,
quoted identifiers, domains, native UUID, generated columns, partitions,
schema/table selection, duplicate rules and conflicting patterns.
Document character-based masking and limits; do not imply format validation.

Test new presets through every supported output path. Include valid and invalid
FK subsets and controlled concurrent writes proving snapshot behavior.
Run upstream pg_dump regressions against a matching vanilla build; document
intentional differences. Unsupported combinations fail explicitly.
Acceptance: restored values/types/constraints match expectations, no alternate
format leaks unmasked values. Correct examples in both guides, including UUID
mask length. Never replace expected values merely to make a test pass.

## B — Versions, provenance and releases

### B1: Version manifest and package identity

At implementation time verify upstream version/security information:
https://www.postgresql.org/support/versioning/
PG13 reached EOL on 2025-11-13; label it legacy, not a maintained security target.
Update PG17 to its then-current minor. Add the current stable major (PG18 at
planning time) only after patch/build/restore checks; do not just relax guards.

Centralize supported sources, hashes, majors and OS matrix in a machine-readable
manifest used by CI, packaging and documentation. Test client/server pairs and
cross-major restore limitations. Reject newer-server/older-client combinations.

Separate project SemVer, upstream PG version and native package revision.
Expose them with source commit through build information. Prove native upgrade
ordering when only the project version changes. Before adding another major,
document the default-command ownership policy (currently PG18), and test
coexistence, upgrade and removal without changing system clients.
Acceptance: no scattered contradictory version lists; tested support matrix;
current source checksums; project-version upgrades recognized by apt/rpm.

### B2: Supply-chain and CI hardening

Validate source hashes. Pin Actions to reviewed immutable commits and arrange
update proposals. Default permissions read-only; grant publish/attestation rights
only to relevant jobs. Untrusted PR jobs have no secrets or publication rights.
Add timeouts and concurrency rules that do not cancel stable publication.

Produce checksums, bundled-component SBOM and signed provenance for final assets.
Document verification, including a tampered-file rejection check:
https://docs.github.com/en/actions/concepts/security/artifact-attestations
Checksums alone do not authenticate origin. Do not claim reproducible builds
without demonstrating byte-for-byte reproducibility separately.

Acceptance: complete expected asset inventory, no filename collisions, validated
archive paths/symlinks and runtime dependencies, verifiable source/commit identity,
SBOM and successful provenance verification on downloaded assets.

### B3: Candidate-to-stable procedure

Target v2.0.0-rc.1 then v2.0.0: strict masking changes existing script behavior.
Reserve v1.2.x for compatible fixes if necessary. Never move a published tag.

1. Select a clean commit; finalize CHANGELOG, migration notes and support matrix.
2. Build once. Run integration, upstream and package checks for every claimed target.
3. Publish a prerelease with immutable assets, checksums, SBOM and provenance.
4. Independently download/install/restore those assets without compiler tools.
   Fixes require another candidate; unresolved required checks block stable.
5. Promote exactly those verified bytes to stable. Candidate identity belongs
   to the release channel; packages carry the final target native version so
   promotion needs no repacking. Explain this to prerelease users.
6. Verify stable downloads/attestations and tag-to-source identity; update links.

Rollback: publish an advisory, direct users to the previous verified release,
and issue fixes under a new version. Never silently overwrite bad assets.
Test package downgrade instructions; database rollback is not part of this process.
Acceptance: a failed gate cannot publish stable, including manual dispatch;
promoted hashes match candidate hashes; user-facing version/support claims match.

## C — Profiles and typed pseudonymization

### C1: Versioned profile files

Proposed --profile=FILE reads UTF-8 JSON with schema_version: 1.
filters: array of {table, where}; masks: array of {table, column, preset} or
{table, column, expression}. Exactly one of preset/expression is required.

Strict parser: reject unknown fields, duplicate JSON keys, unsupported versions,
oversized/deep input. No environment expansion or includes. Profiles contain SQL
and must be trusted; they are not safe executable content from arbitrary sources.
Compiled client parses directly, without Python. Review upstream frontend JSON
facilities first; document parser dependency/license/size if one is needed.

Merge CLI/profile into the same rule model. Duplicate masks fail; filters AND.
No silent order-based overrides. Profiles and CLI use identical dry-run semantics.
Acceptance: equivalent profiles/CLI restore identical data; invalid profiles fail
before export; Unicode and paths with spaces work; no contents/secrets in logs.
Provide CI-tested synthetic tenant, support and full-redaction example profiles.

### C2-design and C2: Typed deterministic pseudonyms

Write `docs/design/pseudonymization.md` before implementation. Specify threat model,
execution location, canonicalization, domains, key lifecycle, collision strategy,
parallel behavior and supported target types. Use reviewed keyed primitives;
plain unsalted hashes must not be advertised as privacy protection.

Keys must not appear in CLI literals, profiles, generated SQL, server/client logs,
dump metadata or attestations. Design a protected file/FD input interface and
audit leakage paths. If server SQL cannot keep keys out of logs or needs an
extension, resolve the architecture before coding; preserve the product contract.

Same canonical input/key/domain gives the same result across tables and workers.
Different keys/domains separate results. Preserve NULL; specify empty behavior.
Do not truncate hashes into integer keys and assume uniqueness. Select initial
supported types after review; test real UUID/text/email targets, reject others.
Verify actual FK/unique constraints after restore; document collisions/cross-type
limitations. Call the feature pseudonymization, not irreversible anonymization.

Acceptance: reviewed design with no unresolved key-handling blockers; deterministic
serial/parallel tests, key/domain separation, typed/FK roundtrips and no leakage.
This is separate from partial star masking and need not block v2.0.

## D — Distribution and adoption

### D1: Native ARM64 and macOS

First Linux ARM64 with native build and runtime-only tests; then macOS arm64/x86_64
with minimum OS policy, linked-library inspection and relocatable packages.
Add Homebrew tap after native install/upgrade/remove passes.
Cross-compilation alone is not verified support. Select runners within available
budget; do not advertise untested architectures. Every target also needs restore tests.

### D2: Signed APT/RPM channels

Prepare candidate/stable channels, immutable packages, signed repository metadata,
retention, key rotation and rollback. Test fresh install, previous-release upgrade,
pinning, signature rejection and removal in disposable runtime containers.
Retain standalone downloads.

Hosting/domain cost and signing-key ownership require an owner decision before
external provisioning. Continue local metadata generation/testing while blocked.
Do not substitute a curl-to-shell installer.
Acceptance: hosted signed channel, verification instructions and real fresh-client
installation/upgrades. Local generation alone does not complete publication.

### D3: Accurate documentation and launch assets

Remove the keyword-stuffed "If you searched for" paragraph. Use natural PostgreSQL
dump/filter/masking terminology. Avoid unqualified drop-in, privacy-safe and
anonymized claims. Explain partial masking and restore limits near examples.
English README is primary; Turkish guide stays synchronized. Mark unreleased
features until a corresponding stable release exists.

Add support matrix, CHANGELOG, CONTRIBUTING, SECURITY and issue templates asking
for redacted examples. Use an actual security-reporting route, no invented email
or response SLA. Include a tested upstream comparison distinguishing object
selection from row filtering, and simple package-selection instructions.

After a release, prepare a short synthetic terminal demo and three tutorials:
tenant subset, date-range export, masked customer extract. Run every command
against release binaries. Prepare launch drafts for PostgreSQL communities;
do not post or message people without explicit authorization. Track aggregate
downloads, installation issues and feedback; promise no rankings/star counts.

## Verification, definition of done and handoff

| Gate | Required evidence |
| --- | --- |
| A | Strict failures, dry-run parity, formats, edge-case and upstream checks |
| Stable v2.0 | A + B1/B2 + release docs; candidate/stable hash and provenance validation |
| C1 | Parser negatives, CLI equivalence, tested examples |
| C2 | Reviewed architecture, type/FK/key isolation and leakage tests |
| New platform | Native install/upgrade/remove and dump/restore |
| Package repository | Published signed metadata, clean-client install and upgrade |

Existing commands (substitute isolated candidate paths):
- git diff --check
- python3 -m py_compile scripts/apply_pgdumpplus.py scripts/package_client.py scripts/verify_isolated.py
- python3 scripts/test_patcher.py /path/to/pristine/upstream
- python3 scripts/verify_isolated.py --binary /path/to/pg_dumpplus --vanilla /path/to/pg_dump --pg-restore /path/to/pg_restore

Read smoke_package.sh before execution: it installs/removes packages.
Python syntax checks alone do not verify generated C or SQL.
Documentation-only changes need link/example consistency review, not full builds.

Every completion records task ID, checked commit/run, observed result, tested
platforms, migration impact and unresolved limits in TASKS.md.
Every interruption records active ID, changed paths, last check and exact next step.

# Agent handoff

Read PLAN.md for the specification and TASKS.md for state before development.
README describes product usage, not authorization or future feature completion.

- Repository: https://github.com/senhakan/pgdumpplus
- Product/command: pg_dumpplus; native package prefix: pgdumpplus.
- Current stats request: read docs/design/export-stats.md and the S0–S6 queue in
  TASKS.md. Planning alone does not authorize implementation. After authorized
  experiments, S4 requires the owner's continuation decision based on timing data.
- Otherwise choose the applicable READY task in TASKS.md; follow dependencies.
- Preserve compiled client installation: no client-side Python/compiler or server extension.
- Python patches upstream C; revised patches require pristine source trees.
- Preserve user edits, existing databases, installed clients and cleaned Git history.
- Keep secrets/private hosts/real datasets out of tracked files, logs and assets.
- Runtime tests use owned disposable synthetic databases. Package tests use disposable environments.
- Validate generated C and restored values for behavior changes; Python syntax alone is insufficient.
- Documentation-only changes need consistency/link checks, not unnecessary runtime builds.
- Update TASKS.md with commit/run evidence, limitations and exact next step at handoff.
- A main CI pass is not a published release. Apply PLAN release gates before claiming completion.
- A planning/review request does not authorize implementing the roadmap.
- Follow existing user authority for routine work. New hosting costs and public community messages
  require their applicable explicit authorization.

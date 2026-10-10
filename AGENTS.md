# Repository guidance

underwrite is a local evidence CLI and Python library. Keep changes focused on
observable behavior and verify claims against tests and the implemented contracts.
Build for coding agents working with local artifacts: a documented CLI and importable
Python API, typed JSON results, and explicit next actions. Do not expand the product
into Docker, a server, or a web interface. See [AGENT_USAGE.md](AGENT_USAGE.md)
for the agent-facing workflow.

- `packages/underwrite-core/src/underwrite_core`: dependency-free integrity primitives.
- `src/underwrite/instrument/evidence`, `measurement`, `acceptance`, and `adjudication`:
  pure logic importing only the standard library and `underwrite_core`.
- `src/underwrite/instrument/ingest`, `fleet`, `service`, and `cli`: effectful adapters.
- `contracts`: JSON Schema contracts; installed schemas must match these resources.
- `tests` and `packages/underwrite-core/tests`: application and core tests.

Preserve import boundaries in `.importlinter`, strict typing, and explicit unknown
states. `not_measured`, `indeterminate`, and `abstained` have different meanings;
never turn missing evidence into a successful verdict. Treat source verdicts as
source claims and content hashes as byte identity, not independent provenance.

Before reporting a change complete, committing, or pushing, use the
[underwrite-preflight skill](.codex/skills/underwrite-preflight/SKILL.md).
Run `uv run --frozen --no-sync python scripts/preflight.py` after the final edit;
the runner owns local and CI check selection. Use `--mode full` for releases.
Do not treat a plan, stale report, skipped check, or failed run as validation.
A passing run covers subsequent commit/push of the unchanged files.
Add dependencies only when necessary. Do not add scheduled CI workflows.

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

Use `uv sync --frozen --all-groups`, then run `uv run --frozen --no-sync` with
`python -m pytest`, `ruff check`, `pyright`, `lint-imports`, and `deptry .`.
Build both distributions with `uv build --all-packages`. Add dependencies only
when necessary. Do not add scheduled CI workflows.

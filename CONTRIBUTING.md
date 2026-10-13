# Contributing

Bug reports, documentation improvements, and pull requests are welcome. For a bug,
include a small reproducible example, your Python and underwrite versions, the
command you ran, and the result you expected. Remove credentials and private data
from examples. Report security vulnerabilities using [SECURITY.md](SECURITY.md).

For a change, fork the repository, create a branch, and submit a pull request that
explains the problem and resulting behavior. Keep changes focused and add tests
for changed behavior. Ordinary Git commits are sufficient; no special author
trailers or contributor agreement are required.

## Development

Use Python 3.12 or later and [uv](https://docs.astral.sh/uv/):

```sh
uv run --frozen --no-sync python scripts/preflight.py
```

Run targeted regression tests while editing, then run preflight after your final
edit and before committing or pushing. It synchronizes frozen dependencies and
selects the checks from the committed changes against `origin/main`, plus staged,
unstaged, and untracked files. Use `--base <ref>` for another review base, `--plan`
to inspect selection without running checks, or `--mode full` for a complete run.

Code, contracts, dependencies, automation, unknown paths, and uncertain comparisons
receive the full suite: lint, strict typing, import/dependency boundaries, tests,
both distribution builds, an installed-wheel CLI workflow outside the checkout,
and pure tests in an environment without application dependencies. A narrow
allowlist of documentation changes gets licensing and packaged-resource tests,
both builds, and the installed-wheel workflow. The allowlist and commands live in
`scripts/preflight.py`; there is no manual documentation-only bypass.

The runner stops on failure and writes local diagnostics to the ignored
`output/preflight.json`. A report describes that run, not future edits. Do not
submit it as proof that CI can be skipped. Correct failures before pushing; an
unchanged, verified tree does not need another run merely to create its commit.

GitHub runs the same checks in the stable `checks` job. PRs use automatic selection;
pushes to `main`, manual CI runs, and release preparation use the full lane.
Superseded PR runs are cancelled. There are no scheduled workflows, additional
runner matrices, or routine artifact uploads. The existing uv download cache is
reused. Standard GitHub-hosted runner compute for public repositories is currently
free; larger runners and storage have separate billing rules. See
[GitHub Actions billing](https://docs.github.com/en/billing/concepts/product-billing/github-actions).

Codex and Claude Code can discover the shared `underwrite-preflight` skill through
their repository skill directories. Its single source is
[SKILL.md](.codex/skills/underwrite-preflight/SKILL.md); `AGENTS.md` makes it the
default completion workflow and `CLAUDE.md` points to those same instructions.

Tests cover both the application and `packages/underwrite-core`. Keep pure modules
free of application dependencies and filesystem, network, or clock effects.
See [AGENTS.md](AGENTS.md) for repository layout and technical constraints.

## Implementation owners

The optional workspace policy loaded by AGENTS.md owns shared development and
quality criteria for goppi and underwrite. This table maps those criteria to
underwrite's implementation; it does not copy the shared policy or introduce
another gate. Standalone contributors use this guide and AGENTS.md without a
sibling checkout. Keep the observable contract stable during refactoring and
record why a genuinely different boundary needs a new implementation.

For a common module, identify the shared responsibility, observable contract,
invariants, failure meaning, and reason for change. Code can implement the same
rule differently; similar code can also represent different domain concepts.
Reuse the existing owner when the meanings agree, and document a meaningful
difference before creating another path. A number called a score is not enough to
combine task outcomes, calibration, statistical evidence, or decision policy.

Use the existing functional core and effectful shell. Pure computations depend on
domain contracts; CLI, filesystem, network, clock, and process adapters handle
technical interaction. Apply a small port at a concrete interaction or substitution
boundary when useful, without requiring an interface for every function or a DI
framework. [Ports and adapters](https://alistair.cockburn.us/hexagonal-architecture)
describes this separation; it is a design pattern, not a formal conformance standard.
Keep producer-specific semantics at their existing owners and test the shared
contract, including failure and interpretation boundaries, before refactoring.

| Concern | Existing owner | Maintain the boundary |
| --- | --- | --- |
| Bounded local input | `_fileio.read_descriptor`, `instrument/ingest/transport.decode_json`, `inspection.read_at`, `local_capture.RootReader` | Reuse strict decoding and descriptor reads; captured-byte flows must not reopen source paths for storage |
| CLI results and errors | `cli/evidence.py`, `service/_record_errors.py` and its CLI/schema projections | Reuse the emitter and owned diagnostics; distinguish analysis, storage, and process exits |
| Composed record responses | `cli/record_capture.py` | Share response admission, storage verification and storage wording; retain producer-specific analysis, input selection and meaning in their command adapters |
| Paired calculation and review | `instrument/evidence/paired_review.evaluate_paired_review` | Calculate once; presentations project results rather than derive new verdicts |
| Measurement meaning and read binding | `measurement_contract` | Share semantic identity and read invariants below independent pure domains; measurement owns statistics, acceptance owns claim binding and classification |
| Event policy preparation and validation | `measurement/event_policy` | Prepared and supplied policies use one validator; preparation leaves assumptions unknown and requires explicit consumer choices |
| Source joins | The exact profile's ingest adapter and pure evidence verifier | Keep path/byte access separate from semantic joins; do not execute supplied schemas or source code |
| Contracts and packaged resources | `contracts/`, `_contracts` links, single-purpose schema generators | One authored owner; generate composed views and verify installed bytes rather than hand-copy fields |
| Immutable records | `service/records.save_record`, `save_captured_record`, and `read_record_file` | Share publication and selected-byte read owners; publication/readback failures remain failures, and selected-file integrity is distinct from whole-record verification and provenance |
| Capability discovery | `cli/main.py` command descriptors and the trusted codec registry | Advertise actual registered paths and installed schema resources; a package version is insufficient |
| Completion checks | The preflight skill and `scripts/preflight.py` | Use the maintained selector after the final edit; do not add a competing command list |

Include the observed user result, relevant regression/entry-point evidence,
refactoring rationale (including when no change is justified), independent review
for material boundaries, and remaining gaps in the PR. Test counts alone do not
establish maintainability, human time savings, or superiority over another tool.

By intentionally submitting a contribution for inclusion, you agree that it is
provided under Apache-2.0, unless explicitly stated otherwise, as described in
section 5 of [LICENSE](LICENSE). The adapted community and trademark policy
texts retain their [file-specific licenses](LICENSING.md).
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

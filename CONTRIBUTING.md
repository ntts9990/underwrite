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

GitHub runs full preflight in the stable `checks` job for every PR and manual CI
run; release preparation also uses the full lane. Local preflight retains its
automatic selection. The upstream repository protects `main` with a PR rule,
zero required human approvals, and strict required checks from the GitHub Actions
and GitGuardian apps. Keep branches current with `main`; normal merges require
both `checks` and `GitGuardian Security Checks` to succeed. There are no routine
bypasses, direct pushes, force pushes or deletions of `main`.

Automatic CI runs on PRs, without a duplicate full run after merge. A documentation
PR also receives full remote validation; abandoned PRs and stale-branch refreshes
can therefore use more checks than the former selected PR lane. Fork maintainers
must configure their own protection. If removing protection, first restore the
`push` trigger for `main` through a checked PR and verify the restored main check.
Never remove protection first to work around an unavailable required check.
Manual runs remain diagnostic coverage, not a substitute for a successful PR run.

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

## Producer compatibility

Normal intake should interpret supported format families without asking users to
choose producer package versions. Keep exact version/profile options for legacy
reproduction. Distinguish a file's declared producer version (or unknown) from
Underwrite's reader policy; structural compatibility does not identify or
authenticate the producer. A compatible release string alone is not an admission
gate, and accepting its shape does not certify an untested release.

Maintain a latest-stable native fixture check and older regression examples when
changing an adapter or preparing a release. Preserve truthful fixture provenance.
Validate the fields and populations the task consumes, tolerate unconsumed additive
fields with an explicit selected scope, and reject unsupported format generations
or changed critical semantics without trying older readers to conceal the failure.
Keep producer execution, runtime network lookups and updates outside input parsing.
Test no-selector audit and same-byte record workflows as well as explicit legacy
contracts. Document any default output-schema migration; do not alter old stored
contracts to pretend they are new results.

## Implementation owners

The optional workspace policy loaded by AGENTS.md owns shared development and
quality criteria for goppi and underwrite. This table maps those criteria to
underwrite's implementation; it does not copy the shared policy or introduce
another gate. Standalone contributors use this guide and AGENTS.md without a
sibling checkout. Keep the observable contract stable during refactoring and
record why a genuinely different boundary needs a new implementation.

Decide whether Underwrite should own a feature before assigning its module.
Underwrite's distinctive result is comparable, missingness-aware evidence and
the next verification task across producer formats. It may own a domain
measurement when that meaning and evidence boundary are its distinctive work;
it does not need to own every statistic or every useful agent feature. A producer
such as goppi owns its execution capture, native trace/verifier binding, trace
export, proposals, and controlled reruns. The calling agent can write narrative,
label hypotheses, and prepare a patch through the producer's review path.

For a proposed integration, record the user's concrete task, the producer-owned
artifact/operation, and the smallest handoff that lets Underwrite check it.
Underwrite still validates bounded input and selected raw-byte identity. It checks
manifest bindings, cross-references, roster and coverage when the chosen contract
declares them, and owns the meaning of its comparison. Producer-native verification
stays a source claim. Independently reperforming a calculation can establish
calculation agreement, not authenticate producer execution or the truth of
source claims.
If that narrow handoff cannot deliver the result, explain the
observed gap before adding an Underwrite implementation; compare user steps,
semantic correctness, maintenance cost, and measured result, not code volume.
For example, ask goppi to export its already bound case trace and verifier facts
before porting its digest and sort rules to Python. A version-specific local
adapter may still be warranted once a portable product handoff exists.

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
| Bounded local input | `_fileio.read_descriptor`, `_json.decode_json` (re-exported by ingest transport), `cli.local_json.read_source`, `inspection.read_at`, `local_capture.RootReader` | Reuse strict decoding and descriptor reads; captured-byte flows must not reopen source paths for storage |
| CLI results and errors | `cli/evidence.py`, `service/_record_errors.py` and its CLI/schema projections | Reuse the emitter and owned diagnostics; distinguish analysis, storage, and process exits |
| Composed record responses | `cli/record_capture.py` | Share storage-subdocument validator construction, response admission, storage verification and storage wording; retain producer-specific analysis, input selection and meaning in their command adapters |
| Paired calculation and review | `instrument/evidence/paired_review.evaluate_paired_review` | Calculate once; presentations project results rather than derive new verdicts |
| Measurement meaning and read binding | `measurement_contract` | Share semantic identity and read invariants below independent pure domains; measurement owns statistics, acceptance owns claim binding and classification |
| Event policy preparation and validation | `measurement/event_policy` | Prepared and supplied policies use one validator; preparation leaves assumptions unknown and requires explicit consumer choices |
| Source joins | The exact profile's ingest adapter and pure evidence verifier | Keep path/byte access separate from semantic joins; do not execute supplied schemas or source code |
| Contracts and packaged resources | `contracts/`, `_contracts` links, single-purpose schema generators | One authored owner; generate composed views and verify installed bytes rather than hand-copy fields |
| Immutable records | `service/records.save_record`, `save_captured_record`, and `read_record_file` | Share publication and selected-byte read owners; publication/readback failures remain failures, and selected-file integrity is distinct from whole-record verification and provenance |
| Capability discovery | `cli/main.py` command descriptors and the trusted codec registry | Advertise actual registered paths and installed schema resources; a package version is insufficient |
| Completion checks | The preflight skill and `scripts/preflight.py` | Use the maintained selector after the final edit; do not add a competing command list |
| Installed-wheel environment and identity | `scripts/_wheel_smoke.py` | Own subprocess isolation, installation and archive identity checks; keep CLI task scenarios and orchestration in `scripts/smoke_wheels.py` |

Include the observed user result, relevant regression/entry-point evidence,
refactoring rationale (including when no change is justified), independent review
for material boundaries, and remaining gaps in the PR. Test counts alone do not
establish maintainability, human time savings, or superiority over another tool.

## Releasing the application and core

Keep versions unchanged until the maintainer explicitly requests a version
increase. Publish only after explicit release authorization. Prepare and verify
the changes through the protected PR flow before dispatching `Publish PyPI` on
`main`.

The workflow defaults to an application-only release (`publish_core: false`).
It keeps full checks of both local packages and additionally installs the exact
candidate application with a core resolved from PyPI. A missing or incompatible
published core blocks that path. Select `publish_core: true` only for an explicitly
approved core release; its successful upload precedes the application upload.
Do not increment core merely to match the app, ignore a failed core upload, or
re-upload changed bytes under an existing version. Identical source code can
produce different archive hashes when rebuilt.

After publication, verify registry file identities and a fresh installation of
the actual released version. A development wheel carrying the previous version
does not establish a successful version upgrade.

By intentionally submitting a contribution for inclusion, you agree that it is
provided under Apache-2.0, unless explicitly stated otherwise, as described in
section 5 of [LICENSE](LICENSE). The adapted community and trademark policy
texts retain their [file-specific licenses](LICENSING.md).
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

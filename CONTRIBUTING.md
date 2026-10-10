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

By intentionally submitting a contribution for inclusion, you agree that it is
provided under Apache-2.0, unless explicitly stated otherwise, as described in
section 5 of [LICENSE](LICENSE). The adapted community and trademark policy
texts retain their [file-specific licenses](LICENSING.md).
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

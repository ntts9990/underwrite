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
uv sync --frozen --all-groups
uv run --frozen --no-sync python -m pytest
uv run --frozen --no-sync ruff check
uv run --frozen --no-sync pyright
uv run --frozen --no-sync lint-imports
uv run --frozen --no-sync deptry .
uv build --all-packages
```

Tests cover both the application and `packages/underwrite-core`. Keep pure modules
free of application dependencies and filesystem, network, or clock effects.
See [AGENTS.md](AGENTS.md) for repository layout and technical constraints.

By intentionally submitting a contribution for inclusion, you agree that it is
provided under Apache-2.0, unless explicitly stated otherwise, as described in
section 5 of [LICENSE](LICENSE). The adapted community and trademark policy
texts retain their [file-specific licenses](LICENSING.md).
Please follow the [Code of Conduct](CODE_OF_CONDUCT.md).

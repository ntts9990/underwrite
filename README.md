<div align="center">

<h1><img src="assets/brand/underwrite-logo.png" alt="underwrite" width="640"></h1>

**Turn evaluation artifacts into evidence you can inspect.**

Inspect supported local artifacts, measure native evidence bundles under explicit
policies, and classify change candidates from declared reads.

[![CI](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml/badge.svg)](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/underwrite)](https://pypi.org/project/underwrite/)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)](https://pypi.org/project/underwrite/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

**English** · [한국어](README.ko.md)

[Install](#install) · [Quickstart](#quickstart) · [Agent use](AGENT_USAGE.md) · [Local records](STORAGE.md) · [Supported formats](#supported-formats) · [CLI](#cli) · [Contributing](CONTRIBUTING.md)

</div>

## Why underwrite?

An evaluation result needs context before it can support a change. underwrite is a
Python CLI and library that connects local evidence to explicit measurement and
acceptance policies, while keeping missing evidence visible.

- **One observation contract.** Normalize supported payload profiles without turning source claims into measured conclusions.
- **Explicit measurement.** Bind a binary event, direction, expected population, and statistical assumptions to a policy.
- **Visible uncertainty.** Keep `not_measured`, `indeterminate`, and `abstained` distinct; inspect the result before acting.

```text
Native evidence bundle → ingest → observation → measure + policy → read
Candidate + claims + supplied reads → accept → classification

Supported external export → ingest → observation (source claims and byte identity only)
```

A supported external observation does not enter the current measurement path.
With a valid policy, `measure` returns `UNSUPPORTED_PROFILE` and produces no read. See
[Agent use](AGENT_USAGE.md) for an explicit example.

A content hash identifies bytes; it does not establish independent provenance.
Exit `0` means a command produced its result, not that a change is approved.

## Install

Requires Python **3.12+** and [uv](https://docs.astral.sh/uv/).

For current measurement, accounting, records, and handoff workflows, use the [source checkout
quickstart](#quickstart). The command below installs the older published `0.0.1`,
which does not include those commands or `capabilities`. Development packages
still carry `0.0.1`; equal version numbers do not establish equal capabilities.

```sh
uv tool install 'underwrite==0.0.1'
underwrite --help
```

Both [underwrite](https://pypi.org/project/underwrite/0.0.1/) and
[underwrite-core](https://pypi.org/project/underwrite-core/0.0.1/) are available on
PyPI as wheels and source distributions. The core package uses only the Python
standard library; application adapters handle local I/O and schema validation.

## Quickstart

Run the complete native path with the included synthetic example from a source
checkout. No adjacent repository checkout is needed. `uv sync` uses the matching
local `underwrite-core` workspace package. If installing development wheels, use
app and core wheels built from the same checkout; do not mix the development app
with the published core merely because their versions match.

```sh
git clone https://github.com/ntts9990/underwrite.git
cd underwrite
uv sync --frozen --all-groups
mkdir -p output

uv run --frozen --no-sync underwrite ingest \
  --format underwrite.evidence-bundle --version v1 \
  --file fixtures/examples/event-measurement/bundle.json --json \
  > output/observation.json

uv run --frozen --no-sync underwrite measure \
  --observation output/observation.json \
  --policy fixtures/examples/event-acceptance/policy.json --json \
  > output/read-event.json

uv run --frozen --no-sync underwrite accept \
  --candidate fixtures/examples/event-acceptance/candidate-single.json \
  --claims fixtures/examples/event-acceptance/claims.json \
  --read output/read-event.json --json
```

This constructed example measures a synthetic binary event proportion, not general
quality or latency. The `read.v2` is computed from the bundle and
`measurement_policy.v3`; calibration diagnostics do not vote on its verdict.
See [measurement meaning and migration](AGENT_USAGE.md#measurement-meaning-and-migration). The final result is
`screened`, requires human review, and keeps `merge_authorized` false. Repeat
`--read` for each required claim. A successful classification does not authorize a
merge or deployment.

For a coding agent working with local exports, see [Agent use](AGENT_USAGE.md) for
commands, result handling, and a reusable task prompt. The external formats below
can be ingested, but `measure` currently accepts only native evidence-bundle
observations; ingestion alone does not make an external source claim measurable.

## Supported formats

Choose the format and exact supported version explicitly.

| Producer | `--format` | `--version` |
| --- | --- | --- |
| DeepEval | `deepeval.test-run` | `4.1.1` |
| Inspect AI | `inspect.eval-log` | `0.3.263` |
| Langfuse | `langfuse.observations-v2` or `langfuse.scores` | `4.35.0` |
| OpenInference | `openinference.traces` | `0.1.38` |
| OTLP JSON | `otlp-json.traces` | `0.160.0` |
| promptfoo | `promptfoo.eval-output` | `0.123.0` |
| Native evidence bundle | `underwrite.evidence-bundle` | `v1` |

These are bounded payload profiles, not compatibility claims for every export or
version of a producer. The native bundle declares
`schema_version: underwrite.evidence-bundle.v1` and carries readings, source claims,
and availability records. The observation retains source claims separately from
measurement.

`project` is an explicit alias of `ingest`. `--http-body-file FILE` reads an already
captured HTTP body from a local file; it does not fetch a URL. Optional
`--source-ref` and `--source-locator` describe the supplied source. Without a source
reference, byte identity supplies a content hash. See `ingest --help` for input
size and depth limits.

<details>
<summary>Try malformed input: typed error, empty stdout</summary>

An empty run identifier is invalid. This example captures the error without
producing an observation:

```sh
mkdir -p output
printf '%s\n' '{"schema_version":"underwrite.evidence-bundle.v1","run_id":"","sources":[],"readings":[],"availability":[]}' \
  > output/malformed-bundle.json
if uv run --frozen --no-sync underwrite ingest \
  --format underwrite.evidence-bundle --version v1 \
  --file output/malformed-bundle.json --json \
  > output/malformed.stdout 2> output/malformed.stderr; then
  ingest_exit=0
else
  ingest_exit=$?
fi
printf 'exit: %s\n' "$ingest_exit"
wc -c < output/malformed.stdout
cat output/malformed.stderr
```

Expected: exit `1`, stdout size `0`, and this JSON on stderr:

```json
{"schema":"instrument_error.v1","command":"ingest","code":"INVALID_INPUT","reason":"MALFORMED_EVIDENCE_BUNDLE_PAYLOAD","exit_code":1}
```

</details>

## CLI

The table describes the current checkout. Discover an installed build with
`underwrite --help`; use `capabilities --json` only when listed. Use
`underwrite <command> --help` for options. The quickstart
uses `ingest`, `measure`, and `accept`; these commands provide additional checks:

| Command | Purpose |
| --- | --- |
| `audit-promptfoo FILE --json` | Compare Promptfoo 0.123.0 declared counts with supplied result rows. |
| `audit-langfuse --observations FILE --scores FILE --json` | Account for Langfuse 4.35.0 score kinds and supplied-row associations. |
| `audit-deepeval FILE --json` | Account for DeepEval 4.1.1 case flags, scores, evaluator errors, and unscored metrics. |
| `inspect CASE.json --json` | Inspect a local artifact manifest and its projection. |
| `check RELEASE.json --json` | Check a release manifest's declared evidence linkage and record presence. |
| `absence --root DIR --glob '*.json' --min 1 --json` | Require a nonempty population. |
| `boundary scan DIR --json` | Scan JSON/JSONL strings for configured verdict vocabulary. |
| `drift check --pins-root PINS --siblings-root SOURCES --json` | Compare a supplied pin registry with local source checkouts. |
| `pins refresh --repo NAME --pins-root PINS --siblings-root SOURCES` | Refresh a configured repository's pins from local bytes. |

Artifact and release checks do not establish independent provenance or grant
deployment permission. Pin commands are generic utilities; no preconfigured
source registry is included. Allowlist and exemption evaluation requires explicit
`--now` when applicable. Malformed inputs and missing paths remain configuration
errors rather than passes.

[JSON contracts](contracts/) define each command family's output and errors.

The optional [underwrite-review skill](skills/underwrite-review/SKILL.md) helps a
coding agent select the existing CLI route for your artifact and explain its
limits. See [manual setup](AGENT_USAGE.md#optional-artifact-review-skill); it does
not install a CLI, hook, or background service.

## Development-checkout evidence pilots

This checkout adds producer accounting, immutable records and selected-file
retrieval, declaration comparison, and paired evidence workflows.
`review-pair` adds digest-bound intended changes, declared evaluation history, and
exclusive case-group accounting while preserving the original paired evidence.
They run locally with JSON output and no service. See the reproducible examples
and limitations in [Agent use](AGENT_USAGE.md#source-checkout-pilot-audit-promptfoo-counts).
These are unreleased changes; published PyPI `0.0.1` artifacts are unchanged.
The reports do not grant approval or enter the existing `accept` flow.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development checks and the contribution
process, [SECURITY.md](SECURITY.md) for private vulnerability reporting, and
[AGENTS.md](AGENTS.md) for technical boundaries.

<details>
<summary>Maintainers: future PyPI releases</summary>

Before using OIDC publishing, create the GitHub environment `pypi`, restrict it to
`main`, and configure a PyPI GitHub Trusted Publisher on **each** package with owner
`ntts9990`, repository `underwrite`, workflow `publish.yml`, and environment `pypi`.
Once configured, run **Publish PyPI** manually from `main` in GitHub Actions.

The workflow validates both packages, then publishes core before the application;
only the separate publish job receives OIDC permission. No stored PyPI API token
is required. Retry a partial upload with the same artifacts: existing registry
files must match exactly, and changed artifacts require a new version.

See the official [uv publishing guide](https://docs.astral.sh/uv/guides/package/)
and [PyPI Trusted Publisher guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

</details>

## License

[Apache-2.0](LICENSE). [LICENSING.md](LICENSING.md) records the adapted policy
templates' CC licenses.

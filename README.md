<div align="center">

<h1><img src="https://raw.githubusercontent.com/ntts9990/underwrite/main/assets/brand/underwrite-logo.png" alt="underwrite" width="640"></h1>

### Know what your eval files actually support.

Find mismatches. Keep missing evidence visible. Know what to check next.

**Local CLI & Python library · JSON results · No server or model calls**

[![CI](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml/badge.svg)](https://github.com/ntts9990/underwrite/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/underwrite)](https://pypi.org/project/underwrite/)
[![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-blue)](https://pypi.org/project/underwrite/)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/ntts9990/underwrite/blob/main/LICENSE)

**[Try it](#quickstart)** · [Use with an agent](#agent-use) · [Supported formats](#supported-formats) · [Docs](#documentation)

**English** · [한국어](https://github.com/ntts9990/underwrite/blob/main/README.ko.md)

</div>

Evaluation tools run evaluations and export result files. Underwrite reads the
local files it supports and shows what those files establish, what remains
unknown, and what evidence to check next. For example, it checks whether
Promptfoo’s reported totals agree with its supplied result rows. You or your
coding agent use the JSON result to investigate and decide. You can also save
selected inputs with the result for later review.

Underwrite does not rerun evaluations, independently prove a producer's verdict,
or assign one quality score across tools. Checking supported local files needs
no Docker, dashboard, or provider credentials.

> [!NOTE]
> **This guide targets `underwrite==0.1.1`.** Its CLI and Python workflows work
> without a checkout. The older `0.0.1` artifacts lack these workflows and have
> a separate `pins refresh` caution; see [Install](#install).

Development checkouts advertising `promptfoo.eval-output|auto` can read SummaryV3
with `underwrite audit-promptfoo FILE --json`, without a producer-version option.
This automatic default is newer than the published 0.1.1 package; see the
[input workflow](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-promptfoo-counts).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/); the optional
synthetic download below uses `curl`.
File-based CLI workflows are tested with CPython 3.12 on Linux and macOS. They
require Unix/POSIX file APIs, including for ordinary artifact reads.

**1. Install and check a file.** The app-only install resolves a compatible
`underwrite-core` automatically. Download the public synthetic example; no
checkout or API key is needed:

```sh
uv tool install 'underwrite==0.1.1'
underwrite capabilities --json
example_dir=$(mktemp -d)
curl -fsSLo "$example_dir/promptfoo.json" \
  https://raw.githubusercontent.com/ntts9990/underwrite/cb913d39bce628b93dcd3b06fcc4ce36f9dc66af/fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json
underwrite audit-promptfoo "$example_dir/promptfoo.json" \
  --version 0.123.0 --json
```

The same `uv tool install` command updates an older uv-managed `underwrite`
tool without `--force`. If your shell cannot find `underwrite`, run
`uv tool update-shell` and open a new shell.

**2. Read the finding.** The producer summary declares **3 results**, including
**1 failure**; the file contains **2 rows**, with **0 failure rows**. The command
reports the difference without guessing which side is wrong. Selected fields
from its actual output:

```json
{
  "schema": "promptfoo_accounting.v1",
  "declared_relation": "inconsistent",
  "producer_stats": {"errors": 1, "failures": 1, "successes": 1, "total": 3},
  "row_tallies": {"errors": 1, "failures": 0, "successes": 1, "total": 2},
  "deltas": {"errors": 0, "failures": -1, "successes": 0, "total": -1},
  "findings": [
    {"code": "CATEGORY_COUNT_MISMATCH", "location": "/results/stats/failures", "row_index": null}
  ],
  "limitations": [
    "PLANNED_DENOMINATOR_UNKNOWN",
    "CAUSE_UNKNOWN",
    "SOURCE_CLAIMS_UNVERIFIED",
    "NO_APPROVAL_AUTHORITY"
  ]
}
```

**3. Next check.** Compare the export with the producer's run record and planned
case inventory before claiming that a run was skipped or a failure row is
missing. `CAUSE_UNKNOWN` and `PLANNED_DENOMINATOR_UNKNOWN` make that limit
explicit. Exit `0` means the report was produced, not that the counts agree or
the source's verdict was verified. To keep the input and analysis together,
[save them in a local record](#keep-results-you-can-return-to).

For your own completed Promptfoo export, replace the example path and select its
supported source version. The synthetic download above is a constructed example,
not an unchanged producer export.

<details>
<summary><strong>Try the native measurement → acceptance workflow</strong></summary>

This example uses policies and fixtures in the GitHub checkout; they are not
included in the installed package. To run it, prepare a checkout first:

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

This measures a synthetic binary-event proportion, not general quality or latency.
The `read.v2` uses an explicit `measurement_policy.v3`; calibration diagnostics do
not vote on its verdict. The final classification is `screened`, requires human
review, and keeps `merge_authorized` false. Repeat `--read` for each required claim.
See [measurement meaning](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#measurement-meaning-and-migration).

</details>

## Why underwrite?

| Question after an evaluation | What underwrite gives you |
| --- | --- |
| Does the summary match the supplied records? | Compare Promptfoo declared counts with rows. Separately account for DeepEval scored rows and evaluator errors, or Langfuse score associations; their aggregate summaries are not assumed checked. |
| What is still unknown? | See when planned cases are unknown or source claims are unverified; unknown is not counted as zero. |
| Can this evidence support a measurement? | Use the separate native binary-event path with a declared event and policy; external scores are not a universal quality verdict. |
| Can I revisit the conclusion? | Save selected input bytes and the analysis in a private record, then verify and retrieve them later. |

## How it works

- **Evaluation tool:** runs the evaluation and exports result files.
- **Underwrite:** checks the supplied results, returns JSON with findings and
  unanswered questions, and can save selected original files with the result.
- **You or your agent:** check the missing evidence and decide what to do next.

Each command checks only its selected input profile. Source verdicts remain
source claims; Underwrite does not apply changes or grant merge authority. Its
separate native binary-event workflow measures only a declared event under an
explicit policy, not arbitrary external scores.

## Agent use

Give your coding agent the installed CLI and a completed local export. It can
discover the available routes, execute them, and use the JSON in its response:

```sh
underwrite --help
underwrite capabilities --json
```

Python callers can use
`underwrite.instrument.ingest.accounting.audit_promptfoo_file(Path(...))` for
the same result as a dictionary. Select an exact supported profile before
interpreting any producer-specific field.

Example task for an agent with terminal access:

> Review `/absolute/path/to/completed-promptfoo-export.json` with the installed
> `underwrite` 0.1.1 CLI as a Promptfoo 0.123.0 export. Use `capabilities --json`
> and `audit-promptfoo --version 0.123.0`.
> Report `declared_relation`, `producer_stats`, `row_tallies`, `raw_hash`,
> `limitations`, and the next useful action. Do not rerun the producer or treat
> exit `0` as agreement or approval.

The optional [underwrite-review skill](https://github.com/ntts9990/underwrite/blob/main/skills/underwrite-review/SKILL.md) provides
reusable guidance for Codex and Claude Code. [Setup and usage](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#optional-artifact-review-skill)
are explicit: the skill lives in the repository, not the PyPI package, and does
not install the CLI or add a background service.
The same CLI and importable Python APIs are available to your own automation.

## Supported formats

Support is for the **specific local payload profiles below**, not every API,
export, or version offered by each producer. Check installed capabilities;
explicit selectors reproduce older contracts.

| Your input | Supported profile | Start here |
| --- | --- | --- |
| **goppi** | Completed baseline exchange manifest v2; separate paired-handoff route | [`audit-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-a-completed-goppi-baseline-as-common-evidence) for descriptive common JSON · [`project-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#project-a-completed-goppi-baseline-into-event-observations) for native measurement · [`review-handoff`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#review-a-completed-paired-handoff-in-one-call) |
| **Langfuse** | Published `0.1.1`: `4.35.0` server blob arrays. Development automatic intake: compatible blob arrays or official-CLI list captures. | [`audit-langfuse`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations), with an optional content-minimized row index |
| **DeepEval** | Published `0.1.1`: ordinary `4.1.1` or `4.2.8` TestRun. Development automatic intake: compatible old/current ordinary or selected trace structure. | [`audit-deepeval`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors) |
| **Promptfoo** | Eval output JSON, `0.123.0` or `0.124.1` | [`audit-promptfoo`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#source-checkout-pilot-audit-promptfoo-counts) |
| **External common result** | `evidence_result.v1` JSON from a producer | [`inspect-result`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#inspect-an-external-common-result) for contract screening and availability counts, with optional evidence-item `next_action` and declared metric-binding counts |
| **Native evidence** | `underwrite.evidence-bundle` / `v1` | `ingest` → `measure` → `accept` |
| **Inspect AI** | `inspect.eval-log` / `0.3.263` | `ingest` |
| **OpenInference** | `openinference.traces` / `0.1.38` | `ingest` |
| **OTLP JSON** | `otlp-json.traces` / `0.160.0` | `ingest` |

For completed exports already on your machine, the installed CLI accepts them
directly; select the exact producer profile you have:

```sh
underwrite audit-promptfoo /path/to/promptfoo-output.json --version 0.124.1 --json
underwrite audit-deepeval /path/to/deepeval-test-run.json --version 4.2.8 --json
underwrite audit-langfuse --observations /path/to/blob-observations.json \
  --scores /path/to/blob-scores.json --json
underwrite audit-baseline --exchange /path/to/goppi-baseline-exchange --json
```

Published `0.1.1` defaults DeepEval accounting to `4.1.1`; select
`--version 4.2.8` there. Development builds advertising automatic intake read
compatible TestRuns without a version flag, reporting the producer release as
unknown. Select `--view trace-index` for recorded steps; metric/expectation
evaluation is outside that view. The owner-asserted [metric binding workflow](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors)
still requires explicit `--version 4.2.8` until its separate contract update.
Published `0.1.1` defaults Promptfoo to `0.123.0`; select `--version 0.124.1`
there. Published Langfuse intake defaults to the 4.35.0 server blob export.
Development builds advertising automatic Langfuse intake read compatible blob
arrays or [official-CLI list captures](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#read-official-langfuse-cli-captures-offline)
without `--profile`; the selected reader does not establish the actual producer
release. Their explicit blob/CLI profiles retain historical v1 outputs. The
controlled CLI fixture proves local file-format handling, not live-server
interoperability or complete export.
See [artifact requirements](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#get-the-right-producer-artifact) and
[producer checks](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md) for the tested scope and fixture origins.

Generic `ingest` / `project` normalizes supported exports to an observation;
external observations do not enter the current `measure` path. goppi uses its
explicit exchange adapters: `audit-baseline` produces descriptive
`evidence_result.v1` without a policy or directional verdict, while
`project-baseline` can produce native event observations. `prepare-event-policy`
binds a consumer configuration while leaving statistical assumptions unknown.
For an already produced `evidence_result.v1` file, `inspect-result` checks the
installed contract and item counts without verifying the producer's source or
bundle, or admitting that result to measurement or acceptance.
Its optional action view counts recorded next actions for evidence remediation,
not executed tool calls or an agent trajectory.

## Keep results you can return to

Use `records` when you want the analysis and its selected source bytes retained
together. For a completed local Promptfoo export:

```sh
underwrite records audit-promptfoo /path/to/promptfoo-output.json \
  --version 0.123.0 --root ./review-store --id review-001 --json

underwrite records read-json \
  --root ./review-store --id review-001 \
  --name analysis/accounting.json --json
```

Use a fresh ID for each capture; existing records are never overwritten.
`records verify` checks stored integrity. `records measure` captures a native
measurement, and `records replay-measure` recalculates it from verified saved
inputs using the current engine, without executing the original producer.
For an exact completed goppi baseline v2 directory, `records audit-baseline`
captures its five files and the descriptive result together in one call; see
[the goppi record workflow](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md#retain-a-completed-goppi-baseline).

Records retain raw inputs, which may contain private content. Choose a private
local POSIX filesystem and a backup strategy; `./review-store` is only an example
location, not a backup. [Storage contracts and recovery](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md).

## What the result means

underwrite keeps different kinds of evidence distinct:

| Result | What it establishes |
| --- | --- |
| Source accounting | Relationships among the supplied rows, scores, and declared summaries |
| Byte hashes and record verification | Identity and integrity of selected bytes, not independent provenance |
| Native measurement | A result under the supplied event definition, population, and statistical assumptions |
| Acceptance classification | A policy result from declared claims and reads; no merge or deployment authority |

`not_measured`, `indeterminate`, and `abstained` remain different states. Missing
information is explicit. A source score is not automatically a calibrated
probability, and a successful command is not approval to ship.

## Install

Install the versioned app; its dependency resolves the compatible core. Use
`underwrite --help` and `underwrite capabilities --json` to see the installed
profiles:

```sh
uv tool install 'underwrite==0.1.1'
underwrite --help
underwrite capabilities --json
```

The same `uv tool install` command updates an older uv-managed installation
without `--force`. If the executable is not on `PATH`, run `uv tool update-shell`
and open a new shell.

For the importable Python API, install into a Python 3.12 environment instead:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'underwrite==0.1.1'
.venv/bin/python - <<'PY'
from pathlib import Path
from underwrite.instrument.ingest.accounting import audit_promptfoo_file

result = audit_promptfoo_file(Path("/path/to/promptfoo-output.json"), version="0.123.0")
print(result["declared_relation"])
PY
```

The installed distributions contain runtime code and schemas. The guide,
synthetic fixtures, optional agent skill, and contributor tools live in the
GitHub repository and require a checkout if you want to run those examples.

<details>
<summary>Historical 0.0.1 and its pin-manifest caution</summary>

The older published [app](https://pypi.org/project/underwrite/0.0.1/) and
[core](https://pypi.org/project/underwrite-core/0.0.1/) do not include the
`0.1.0` measurement, accounting, records, handoff, or `capabilities` workflows.
Discover installed commands with `--help`. Do not pair a `0.1.0` app wheel with
the `0.0.1` core.

The published `0.0.1` `pins refresh` should not process an untrusted pin
manifest. A controlled local audit reproduced overwrite of a writable file
outside the pin registry using a crafted manifest. The `0.1.0` code
refuses that path before mutation; the older `0.0.1` artifacts do not include
the correction. The reproduced case required the crafted manifest and a
writable target file.

</details>

## CLI

Use `underwrite <command> --help` for options. Alongside the workflows above,
`inspect` checks artifact manifests, `audit-counts` checks Promptfoo counts through
a manifest with an expected hash, `check` examines release evidence linkage,
`absence` checks population presence, `boundary scan` checks configured verdict
vocabulary, and `drift check` / `pins refresh` work with supplied pin registries.
[JSON contracts](https://github.com/ntts9990/underwrite/tree/main/contracts/) define each command family's results and errors.

For scan and pin resource limits and filesystem-selection caveats, see
[Scan and pin limits](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#scan-and-pin-limits).

<a id="development-checkout-evidence-pilots"></a>

## Documentation

These guides and examples are on GitHub, outside the runtime wheel. The installed
package includes the schemas needed for its CLI and Python APIs.

| Explore | Guide |
| --- | --- |
| Run from an agent, choose inputs, interpret JSON | [Agent usage](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md) |
| Compare declarations, paired evidence, and goppi handoffs | [Evidence workflows](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#source-checkout-pilot-compare-declarations) |
| Save, verify, retrieve, and recalculate | [Local records](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md) |
| Understand producer fixtures and tested boundaries | [Producer evidence](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md) |
| Extend the CLI and pure calculation core | [Contributing](https://github.com/ntts9990/underwrite/blob/main/CONTRIBUTING.md) · [Architecture rules](https://github.com/ntts9990/underwrite/blob/main/AGENTS.md) |

## Contributing

Bring a reproducible artifact, a concrete workflow, or a fix.
[Open an issue](https://github.com/ntts9990/underwrite/issues) with the producer
version and expected result; share only sanitized examples. See
[CONTRIBUTING.md](https://github.com/ntts9990/underwrite/blob/main/CONTRIBUTING.md) for local preflight and PR checks, and
[SECURITY.md](https://github.com/ntts9990/underwrite/blob/main/SECURITY.md) for private vulnerability reports.

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

[Apache-2.0](https://github.com/ntts9990/underwrite/blob/main/LICENSE). [LICENSING.md](https://github.com/ntts9990/underwrite/blob/main/LICENSING.md) records the adapted policy
templates' CC licenses.

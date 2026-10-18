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
> **This guide targets `underwrite==0.2.0`.** [Install the CLI](#installed-package--020)
> for automatic input handling, focused evidence views and retained trajectory review.
> A [source checkout](#development-checkout--current-main) is optional for repository
> examples and development. Use `capabilities --json` to confirm the installed routes.
> Upgrading from 0.1.1? Read the [migration guide](https://github.com/ntts9990/underwrite/blob/main/MIGRATION.md).
> The older `0.0.1` has a separate pin-manifest caution; see [Install](#install).

## Quickstart

Requires **Python 3.12+** and [uv](https://docs.astral.sh/uv/); the optional
synthetic download below uses `curl`.
File-based CLI workflows are tested with CPython 3.12 on Linux and macOS. They
require Unix/POSIX file APIs, including for ordinary artifact reads.

### Installed package · 0.2.0

**1. Install and check a file.** The app-only install resolves a compatible
`underwrite-core` automatically. Download the public synthetic example; no
checkout or API key is needed:

```sh
uv tool install 'underwrite==0.2.0'
underwrite capabilities --json
example_dir=$(mktemp -d)
curl -fsSLo "$example_dir/promptfoo.json" \
  https://raw.githubusercontent.com/ntts9990/underwrite/cb913d39bce628b93dcd3b06fcc4ce36f9dc66af/fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json
underwrite audit-promptfoo "$example_dir/promptfoo.json" --json
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
  "schema": "promptfoo_accounting.v2",
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
    "UNCONSUMED_FIELDS_AND_POPULATIONS_NOT_ANALYZED",
    "SOURCE_HASH_AND_VERSION_LINKABLE",
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

For your own completed Promptfoo export, replace the path and use a
[supported input shape](#supported-formats). The downloaded file is a
constructed example, not an unchanged producer export.

### Development checkout · current main

Use a checkout to work on the source or run repository examples. Run its commands
through `uv run` inside that directory, keeping the installed tool separate. If you
already have a checkout, open its root and start at `uv sync` below:

```sh
git clone https://github.com/ntts9990/underwrite.git
cd underwrite
uv sync --frozen --all-groups
uv run --frozen --no-sync underwrite capabilities --json
uv run --frozen --no-sync underwrite audit-promptfoo \
  fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.json --json
```

This uses the same synthetic counts as above: 3 declared results versus 2 rows.
The automatic reader produces `promptfoo_accounting.v2` and preserves this
fixture's declared `0.123.0` version as an unverified source claim. Inputs without
a version declaration retain an unknown release. No version flag, producer
installation, Goppi, or provider call is needed for this example.
Use this `uv run --frozen --no-sync underwrite` prefix for all checkout commands.
See [included workflows](#included-in-020) for the other routes.

<details>
<summary><strong>Try the native measurement → acceptance workflow</strong></summary>

After preparing the checkout above, run this example from its root. Its policies
and fixtures live in the repository, outside the installed package:

```sh
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

Give your coding agent a completed local export and your chosen entry path.
Have it inspect `capabilities --json` and the command's `--help` using that same
executable: `underwrite` for the published tool, or
`uv run --frozen --no-sync underwrite` from the checkout. Package version alone
does not identify the available workflows.

Python callers can use
`underwrite.instrument.ingest.accounting.audit_promptfoo_file(Path(...))` in
the chosen environment. Automatic intake selects a supported reader from the
input shape without guessing a producer release. Use explicit historical selectors
when reproducing an older contract, as described in the migration guide.

Example task for an agent with terminal access:

> Review `/absolute/path/to/completed-promptfoo-export.json` using our selected
> Underwrite entry path. Inspect its capabilities and help, then use the
> supported accounting route. Use automatic intake when advertised; otherwise
> require evidence of a supported published profile rather than guessing a version.
> Report count differences, input identity, missing evidence, interpretation
> limits, and the next useful check. Do not rerun the producer or treat exit `0`
> as agreement or approval.

The optional [underwrite-review skill](https://github.com/ntts9990/underwrite/blob/main/skills/underwrite-review/SKILL.md) provides
reusable guidance for Codex and Claude Code. [Setup and usage](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#optional-artifact-review-skill)
are explicit: the skill lives in the repository, not the PyPI package, and does
not install the CLI or add a background service.
The same CLI and importable Python APIs are available to your own automation.

## Supported formats

Support covers selected local payloads, not every producer API, export, or
version. Use capabilities from your chosen executable and the linked workflows
for exact input requirements.

### Supported profiles · 0.2.0

| Your input | Supported profile | Start here |
| --- | --- | --- |
| **Goppi** | Completed baseline exchange manifest v2; P7 handoff; adopted paired trajectory | [`audit-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-a-completed-goppi-baseline-as-common-evidence) · [`project-baseline`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#project-a-completed-goppi-baseline-into-event-observations) · [`review-handoff`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#review-a-completed-paired-handoff-in-one-call) · [`audit-trajectory`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#compare-a-goppi-paired-trajectory) |
| **Langfuse** | Compatible blob arrays or official-CLI list captures; automatic pair selection | [`audit-langfuse`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations) |
| **DeepEval** | Compatible TestRun; automatic accounting, optional trace view and meaning bindings | [`audit-deepeval`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors) |
| **Promptfoo** | SummaryV3 eval output; automatic intake and current v2 results | [`audit-promptfoo`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#audit-promptfoo-counts) |
| **External common result** | `evidence_result.v1` or `evidence_result.v2` | [`inspect-result`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#inspect-an-external-common-result) |
| **Native evidence** | `underwrite.evidence-bundle` / `v1` | `ingest` → `measure` → `accept` |
| **Inspect AI** | `inspect.eval-log` / `0.3.263` | `ingest` |
| **OpenInference** | `openinference.traces` / `0.1.38` | `ingest` |
| **OTLP JSON** | `otlp-json.traces` / `0.160.0` | `ingest` |

### Included in 0.2.0

- **Automatic intake:** compatible Promptfoo SummaryV3, DeepEval TestRun, and
  Langfuse blob arrays or [official-CLI list captures](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#read-official-langfuse-cli-captures-offline)
  without selecting a producer version/profile. Actual producer release stays
  unknown when the payload does not establish it; explicit selectors remain for
  reproducing older contracts.
- **Focused views and storage:** selected DeepEval trace structure, version-neutral
  `deepeval_metric_bindings.v2` packs, and Langfuse row-index recording. Follow the
  linked producer workflows above for their separate contracts and limits.
- **Common results:** `inspect-result` also accepts `evidence_result.v2`.
- **Goppi steps:** [`audit-trajectory`](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#compare-a-goppi-paired-trajectory)
  compares recorded tool states and evaluation results from a paired+trajectory
  export, including runs that stopped early. This optional route requires installed
  Goppi for offline verification; it does not run providers, establish causality,
  or measure retry effects. [`records audit-trajectory`](https://github.com/ntts9990/underwrite/blob/main/STORAGE.md#retain-a-goppi-paired-trajectory)
  retains the same inputs and result; `records read-trajectory` reopens them
  without Goppi or the original export.

See [artifact requirements](https://github.com/ntts9990/underwrite/blob/main/AGENT_USAGE.md#get-the-right-producer-artifact) and
[producer checks](https://github.com/ntts9990/underwrite/blob/main/EVIDENCE_REVIEW.md)
for fixture origins and tested scope. A controlled fixture does not prove
live-server interoperability or export completeness.

Moving from published 0.1.1? See the [migration guide](https://github.com/ntts9990/underwrite/blob/main/MIGRATION.md)
for changed default JSON contracts, historical selectors, and existing records.

Generic `ingest` / `project` normalizes supported exports; external observations
do not enter the current `measure` path. Goppi `audit-baseline` produces
descriptive common evidence, while `project-baseline` can produce native event
observations. `inspect-result` screens the installed common-result contract and
counts items without verifying source truth or admitting them to measurement
or acceptance. Its action view counts recorded next checks, not executed tools.

## Keep results you can return to

Use `records` when you want the analysis and its selected source bytes retained
together. This example uses the **published package** with a completed local
Promptfoo export:

```sh
underwrite records audit-promptfoo /path/to/promptfoo-output.json \
  --root ./review-store --id review-001 --json

underwrite records read-json \
  --root ./review-store --id review-001 \
  --name analysis/accounting.json --json
```

Automatic intake needs no producer version flag. In a checkout, prefix both
commands with `uv run --frozen --no-sync`. Use a fresh ID for each capture;
existing records are never overwritten.
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
uv tool install 'underwrite==0.2.0'
underwrite --help
underwrite capabilities --json
```

The same `uv tool install` command updates an older uv-managed installation
without `--force`. If the executable is not on `PATH`, run `uv tool update-shell`
and open a new shell.

For the importable Python API, install into a Python 3.12 environment instead:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python 'underwrite==0.2.0'
.venv/bin/python - <<'PY'
from pathlib import Path
from underwrite.instrument.ingest.accounting import audit_promptfoo_file

result = audit_promptfoo_file(Path("/path/to/promptfoo-output.json"))
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

The workflow validates both packages and defaults to publishing only the app
(`publish_core: false`), after testing it with the published compatible core. Select
`publish_core: true` only for an explicitly approved core release; core upload must
succeed before app upload. The unchanged core remains `0.1.1` for this app release.
Only the publish job receives OIDC permission; no stored PyPI token is required.
Retry partial uploads with identical artifacts. Existing registry files must match
exactly; never re-upload a rebuilt, different archive under an existing version.

See the official [uv publishing guide](https://docs.astral.sh/uv/guides/package/)
and [PyPI Trusted Publisher guide](https://docs.pypi.org/trusted-publishers/using-a-publisher/).

</details>

## License

[Apache-2.0](https://github.com/ntts9990/underwrite/blob/main/LICENSE). [LICENSING.md](https://github.com/ntts9990/underwrite/blob/main/LICENSING.md) records the adapted policy
templates' CC licenses.

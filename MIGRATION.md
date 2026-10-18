# Development migration and released history

## Unreleased checkout: one Promptfoo result family

The current development checkout keeps package metadata at 0.2.0 but removes
Promptfoo's legacy output selection. This change is **not in the published 0.2.0
wheel**. Inspect `underwrite capabilities --json` from the actual installation;
package version alone cannot distinguish a checkout from its published release.

`audit-promptfoo`, `audit-counts`, and the Python bytes/file/row APIs now produce
only accounting/row v2; `records audit-promptfoo` produces only record v2.
Remove `--version` from Promptfoo audit/capture commands and `version=` from their
Python calls. Removed CLI flags return a usage error before acquisition/storage;
removed Python keywords raise `TypeError`. The low-level `ingest/project` source
codec version and manifest source selectors remain input contracts.

`compare-declarations` accepts current accounting v2 only. Regenerate each report
from its original raw source, then bind the canonical digest of that actual new
report in the comparison manifest while preserving the owner's condition/metric
assertions. Do not change schema labels on old reports or reuse old result hashes.
Direct and manifest accounting over the same source bytes agree; the manifest
route additionally enforces contained paths and an optional expected raw hash.

Old Promptfoo result schemas and their generators are removed from the current
package. Saved records remain immutable and readable with `records read-json`
and verifiable with `records verify`, without the retired schemas or a producer.
A new analysis is a new result/record; it never updates an existing stored report.
See [the complete consolidation milestone](CONTRACT_MIGRATION.md) for the remaining
families; their planned changes are not claimed implemented here.

## Unreleased checkout: one Langfuse flow per view

Langfuse audit and capture choose the supported blob-array or CLI-list family
from both input files. Their distinct typed-score, association and collection
meanings remain intact; file shape never establishes a producer release.
Accounting/row reports use v2, accounting capture uses `langfuse_record.v2`, and
row capture now uses `langfuse_row_record.v2` with only current row reports.

Remove `--profile` from audit/capture commands and `profile=` from Python calls.
Removed flags fail before acquisition/storage; removed keywords raise `TypeError`.
Replace the retired `instrument.ingest.langfuse_cli` APIs with
`instrument.ingest.langfuse_accounting.audit_langfuse_files` for accounting,
`audit_langfuse_row_index_files` for file-based rows, or `audit_langfuse_bytes`
with `view="row-index"` for rows from captured bytes. These APIs support the same
compatible CLI shapes automatically. Low-level source codec selectors
are unchanged. Published 0.2.0 still has the old selectors and row-record v1;
check the actual executable's advertised contracts.

No stored record is converted. Read and verify old blob/CLI/row records as their
original bytes without the retired analysis schemas. A new analysis/capture gets
a new record ID; do not relabel an old result or widen its source assertions.
Row analysis minimizes exposed content, while capture still retains both full
private source files. Analysis and storage outcomes remain separate.

## Unreleased checkout: current DeepEval views and meaning packs

Remove DeepEval audit/capture `--version` and public Python `version=` keywords.
Accounting/capture use v3; trace/trace capture and owner pack/bound result/bound
capture use v2. Real no-flaky/flaky field meanings remain distinct, and the
unversioned input still does not establish a producer release. Low-level ingest
and release-linkage input admission are unchanged.

Old v1 binding packs are refused with `DEEPEVAL_BINDING_PACK_SCOPE_UNSUPPORTED`.
The owner must review and reassert a fresh v2 declaration covering the supported
ordinary occurrences; do not drop an old version restriction automatically.
Direct bound-file analysis checks the pack before opening TestRun. Record capture
attempts both roles first, retains complete buffers, and may save an invalid-pack
diagnostic. It checks both source and pack hashes on success. Reopening old
records needs neither retired result schemas nor a producer.

The current generic `evidence_result.v2` is an authored contract consumed by
DeepEval pack/record composition. Inspection remains unchanged in this slice;
its separate consolidation follows in S5. Published 0.2.0 still retains the old
DeepEval selectors and writers; the package metadata has not been bumped.

## Released history: migration from 0.1.1 to 0.2.0

The remainder describes the **published 0.2.0 release**, including historical
selectors that the development checkout above has removed for Promptfoo, Langfuse and DeepEval.

This guide targets **underwrite 0.2.0**. Upgrade the app; the unchanged core remains
0.1.1 and the app keeps its compatible `underwrite-core>=0.1.0,<0.2` requirement.
No record migration or producer re-execution is needed to read existing records.

```sh
uv tool install 'underwrite==0.2.0'
underwrite capabilities --json
```

The examples use the installed CLI. For a source checkout, run `uv sync --all-groups`
and use `uv run --frozen --no-sync underwrite` instead. See [the CLI guide](AGENT_USAGE.md).

## Ordinary inputs no longer require a producer version

For supported local artifact families, omit `--version` or `--profile`:

```sh
underwrite audit-promptfoo results.json --json
underwrite audit-deepeval test-run.json --json
underwrite audit-langfuse --observations observations.json --scores scores.json --json
```

Underwrite selects a reader from the supplied structure. It does not contact the
producer, install tools, or infer the producer's release from that structure.
`reader_profile` identifies Underwrite's interpretation rules. A supplied
`producer_version` is an unverified declaration; absent evidence remains
`producer_version: null` with `producer_version_state: "unknown"`.

Automatic input accepts the supported selected fields, not arbitrary future
formats. Unsupported format generations, mixed Langfuse families, conflicting
critical fields, and unsupported DeepEval expectation populations receive finite
errors. An older reader is not tried to hide the failure. In DeepEval's explicit
trace view, metrics and expectation outcomes remain outside the analyzed scope.

## Update JSON consumers or select the historical contract

Default output contracts have changed. Branch on the returned `schema` and read
its corresponding contract; do not replace a schema label on old JSON.

| Command | Published 0.1.1 default | 0.2.0 default |
| --- | --- | --- |
| `audit-promptfoo` | `promptfoo_accounting.v1` | `promptfoo_accounting.v2` |
| `audit-promptfoo --view row-index` | `promptfoo_row_index.v1` | `promptfoo_row_index.v2` |
| `records audit-promptfoo` | `promptfoo_record.v1` | `promptfoo_record.v2` |
| `audit-langfuse` | `langfuse_accounting.v1` | `langfuse_accounting.v2` |
| `audit-langfuse --view row-index` | `langfuse_row_index.v1` | `langfuse_row_index.v2` |
| `records audit-langfuse` | `langfuse_record.v1` | `langfuse_record.v2` |
| `audit-deepeval` | `deepeval_accounting.v1` | `deepeval_accounting.v3` |
| `records audit-deepeval` | `deepeval_record.v1` | `deepeval_record.v3` |

To reproduce an existing integration, explicitly select its actual historical
reader:

```sh
underwrite audit-promptfoo results.json --version 0.123.0 --json
underwrite audit-deepeval test-run.json --version 4.1.1 --json
underwrite audit-langfuse --observations observations.json --scores scores.json \
  --profile blob-4.35.0 --json
```

Promptfoo also retains `--version 0.124.1` with its v1 accounting/row contracts.
DeepEval retains `--version 4.2.8` with `deepeval_accounting.v2` and
`deepeval_record.v2`. These selections also apply to their corresponding
`records audit-*` paths. Select a historical reader only when appropriate to the
input, not as a workaround for automatic refusal.

Automatic reports distinguish the selected population and reader from producer
identity. Promptfoo v2 accounting no longer emits the legacy
`observation_digest`; retain its raw byte hash instead. Langfuse v2 has separate
blob/CLI branches and collection scope. Review the versioned schema before
assuming a field from the old report is still present.

`compare-declarations` still requires `promptfoo_accounting.v1` for both inputs
and a digest-bound `declared_comparison_case.v1` manifest. Use an appropriate
explicit historical selector to produce those reports, then bind the actual new
report digests. Do not relabel default v2 reports or reuse a manifest bound to
different bytes. `audit-counts` retains its explicit manifest and v1 output.
Low-level `ingest --format ... --version ...` also remains an explicit codec API.

## Metric meaning packs and new views

Without `--version`, DeepEval `--bindings` now takes a
`deepeval_metric_bindings.v2` pack and produces `evidence_result.v2`; its record
command produces `deepeval_bound_record.v2`. For an existing v1 pack, keep
`--version 4.2.8` to retain `evidence_result.v1` and `deepeval_bound_record.v1`.
Create a new v2 pack according to its contract to use the automatic path; changing
the old pack's schema string is insufficient. Definitions remain owner assertions,
and the pack hash identifies bytes rather than independent semantic authority.
`inspect-result` accepts existing v1 and new v2 results by exact schema.

DeepEval `--view trace-index`, Langfuse official CLI captures and row-index record
storage, and Goppi trajectory analysis are new since published 0.1.1. They do not
upgrade existing stored results. Step statuses and local relationships do not
establish retry effects or causality.

For a supported completed Goppi paired trajectory handoff, the 0.2.0 route
retains the seven captured source files and the analysis or supported diagnostic:

```sh
underwrite records audit-trajectory --exchange EXCHANGE \
  --root PRIVATE_STORE --id trajectory-001 --goppi-executable GOPPI_EXECUTABLE --json
underwrite records read-trajectory \
  --root PRIVATE_STORE --id trajectory-001 --json
```

`trajectory_record.v1` separates analysis, verification history, and storage.
`trajectory_record_read.v1` returns the original analysis/history together with
current whole-record integrity checks. Reopening does not need Goppi and reports
`native_verification_performed_now: false`. Use this dedicated reader for trajectory
reports up to their supported 8 MiB bound; generic `records read-json` keeps its
1 MiB selected-file limit. See [storage](STORAGE.md) for retention and error handling.

A working adopted Goppi offline verifier is needed to create a successfully
verified trajectory result. The accepted source implementation and npm package
are distinct: on 2026-10-18, accepted Goppi main declared package version `0.1.0`,
while npm `@ntts9990/goppi` latest was `0.0.1`. Installing that npm release does not
establish availability of this verifier. Reopening a saved record never installs
or runs it.

## Keep existing records unchanged

The stored manifest remains `result_record.v1`. Existing records retain their
original analysis schema and bytes. Read or verify them without rerunning an
analysis:

```sh
underwrite records verify --root STORE --id RECORD_ID --json
underwrite records show --root STORE --id RECORD_ID --json
underwrite records read-json --root STORE --id RECORD_ID \
  --name analysis/accounting.json --json
```

Use `show` to find the actual logical filename for that record. `read-json` returns
a `record_document.v1` envelope containing the stored JSON; it does not translate
that JSON to a newer schema. Python callers can recover exact bytes with
`underwrite.service.records.read_record_file`.

`records verify` checks the whole record and declared direct links.
`read-json` checks only the selected file against its captured manifest; run
`verify` when whole-record integrity is required. It does not
rerun Goppi, execute a provider, authenticate provenance, or establish the truth of
source verdicts. `records replay-measure` is a separate explicit recomputation,
not ordinary readback. To obtain a new analysis contract, rerun the appropriate
Underwrite analysis on retained source bytes and save it under a **new** record ID;
existing IDs are not overwritten. The necessary inputs, policies, and optional
producer verifier must be available for that new analysis.

Record roots contain complete selected source bytes even when the displayed
analysis is content-minimized. Keep them private. Successful analysis, successful
storage, and a successful source task are separate outcomes; inspect the returned
analysis and storage states.

## Supported input scope at this checkpoint

The following fixture evidence was checked on 2026-10-18. It bounds the support
claim; it is not a promise to accept every output of each product or future format.
A newer declared producer release is not rejected just for being newer when its
selected fields still satisfy the supported input contract.

| Producer | Evidence reused | Boundary |
| --- | --- | --- |
| Promptfoo | [0.124.1 selected fields](fixtures/golden/external/promptfoo-0.124.1/SOURCE.md), historical 0.123.0 | The newer fixture is a documented sanitized derivative of actual local output. SummaryV3 structure governs automatic selection; version metadata remains an unverified declaration. |
| DeepEval | [4.3.1 native captures](fixtures/examples/deepeval-auto-4.3.1/SOURCE.md), historical 4.1.1/4.2.8 | Automatic selection does not infer a release. Visible unsupported expectation populations are refused by accounting; trace view excludes their outcomes. `datasetVersion` is not a producer version. |
| Langfuse | [Official CLI 1.2.4 captures](fixtures/examples/langfuse-cli/SOURCE.md), historical blob API snapshot 4.35.0 | The CLI fixture used a controlled loopback. CLI release, API snapshot, and server release are different identities; this does not certify live interoperability with every server, including server v4.63.0. |
| Goppi | [Adopted paired trajectory fixture](fixtures/conformance/goppi-trajectory/SOURCE.md) | Exact fixture bytes and the offline verifier support a bounded handoff contract. Source verdicts remain claims; stage differences do not establish retry effects or causality. |

An input that lacks declared producer identity can still be supported. A missing
file, unsupported format generation, incompatible semantic declaration, and an
unknown producer version are different states. Keep those distinctions in the
calling agent's output and follow the returned `next_action` or `next_actions`
when present.

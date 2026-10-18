# Local result records

`records` stores explicitly selected files in a private, immutable local bundle.
It does not discover inputs, execute an evaluation, or certify provenance.
These commands were introduced in published `underwrite` 0.1.0 and remain in the
0.2.0 release. Use `underwrite records --help` and
`underwrite capabilities --json` to confirm installed profiles; older packages
can differ. A GitHub checkout is needed only for repository fixtures.

For native measurement, retain the exact observation and policy used by the
calculation together with its read or diagnostic:

```sh
underwrite records measure --observation observation.json --policy policy.json \
  --root PRIVATE_STORE --id measurement-001 --json
underwrite records read-json --root PRIVATE_STORE --id measurement-001 \
  --name analysis/read.json --json
```

`measurement_record.v1` keeps analysis and storage independent: exit 0 means a
read was produced and saved (including `not_measured`), 2 means a diagnostic was
saved, and 3 means storage failed while preserving the analysis result or error.
A diagnostic record consumes its ID. Use `analysis/error.json` for an analysis
failure. `invocation.json` records effective display arguments and acquisition
states; it is not an executable recipe or an attestation of a subprocess.
These exits describe the `measurement_record.v1` stdout envelope. Argument,
configuration and response-admission failures instead emit `evidence_error.v1`
on stderr with exit 2 before storing. Inspect the schema and both streams; exit 2
alone does not establish that a diagnostic record exists.

Both explicitly selected files are attempted once. Complete bytes, including
malformed JSON, are retained as `source/observation.bin` and `source/policy.bin`;
an acquisition failure retains no partial file. Analysis preserves ordinary
`measure` error precedence even when the other input also has an acquisition
failure. Raw file hashes and canonical input digests have different meanings.
Two file reads do not establish an atomic producer snapshot.
Input limits remain 4 MiB/depth 64 for the observation and 64 KiB/depth 16 for the
policy. Generated analysis is bounded to 1 MiB including its newline.
`records read-json` retains its 1 MiB input limit; measurement replay uses an
explicit bounded byte read for larger admitted observations.

The selected observation and policy are retained whole and may contain private
content. Payload source pointers are never followed: capturing a generated
baseline observation does not back up the original goppi exchange. No producer,
network request or arbitrary command runs. Output/schema admission precedes
store mutation, IDs are never overwritten, and post-publication failures retain
the known identity in `storage.published_record`. Stored-byte verification is not
measurement reproduction, source authentication or approval.

To recalculate a saved measurement without its original input paths:

```sh
underwrite records replay-measure --root PRIVATE_STORE --id measurement-001 --json
```

This read-only command verifies the fixed saved observation, policy and outcome
against one manifest identity, then uses the current installed measurement code.
It ignores invocation commands/paths and does not run a producer. The
`measurement_replay.v1` report compares complete canonical tagged outcomes:
exit 0 means `match`, exit 1 means `mismatch`. A matching `not_measured` read or
matching deterministic error remains exactly that; matching a diagnostic means
the refusal was reproduced, not that a measurement succeeded.

Complete captured malformed inputs can reproduce analysis diagnostics. Missing
saved inputs cannot recreate an earlier filesystem failure; those captures are
refused as incomplete. Unsupported record/outcome kinds, ambiguous outcomes,
internal/configuration diagnostics and invalid stored reads are also refused.
An internally consistent saved read with different source digests is a comparison
mismatch, not a separate assertion that the source was authenticated.

Refusals emit no comparison stdout and exit 2: `record_error.v1` describes stored
byte retrieval/integrity failures, while `evidence_error.v1` describes replay
interpretation/configuration failures. Inspect both streams and the schema.
Source reads use the measurement limits above, the saved outcome is limited to
1 MiB, and the full response is limited to 2 MiB. Every selected read must report
the initial manifest identity before calculation proceeds. Unrelated files and
direct links are outside this selected-file check; use `records verify` for them.

Agreement establishes only current in-process outcome agreement. It does not
establish the historical code/dependency environment, producer execution, label
truth, statistical assumptions or approval. Floating-point differences across
runtimes can produce a reported mismatch; no hidden tolerance changes agreement.
The command never changes the original record or its `rerun_verification` claim.

For a supported DeepEval TestRun, use
`records audit-deepeval FILE --root ROOT --id NEW_ID --json` to analyze and retain
the same complete bytes with their result or diagnostic and invocation metadata.
In 0.2.0 builds advertising automatic intake, omitted `--version` returns
`deepeval_record.v3` with `deepeval_accounting.v3`. It selects an Underwrite reader
from consumed fields but leaves the actual producer version unknown. Visible
non-null case or metric `expectationsData` is a finite ordinary-analysis refusal;
complete input can still be retained privately with its diagnostic. Exits 0/2/3
distinguish result saved, diagnostic saved, and storage failed. Complete malformed
input is retained; failed acquisition stores no partial source. A diagnostic
still consumes the ID.
Use `records read-json` with `analysis/accounting.json` or `analysis/error.json`
for later reuse. Raw TestRun content is not redacted. See
[the DeepEval workflow](AGENT_USAGE.md#account-for-deepeval-scores-and-evaluator-errors).

Published 0.1.1 and explicit published-0.2.0 selectors can produce old v1/v2
records. The current checkout removes `--version` and Python `version=` and
writes only current v3 accounting records. Old manifests/invocations preserve
their original selection as data and remain readable without retired schemas.
Current metadata names automatic selection and an applied reader only on success.
Missing verdicts and flaky declarations remain distinct; never relabel a record.

For a selected trace review, a 0.2.0 installation advertising automatic
trace intake supports `records audit-deepeval FILE --view trace-index --root ROOT
--id NEW_ID --json`. The `deepeval_trace_record.v2` envelope retains the one
captured source buffer and its actual `deepeval_trace_index.v2` result as
`analysis/trace-index.json`, or the existing diagnostic file on failure. Its
invocation and manifest identify automatic selection and the trace view. It
cannot be combined with `--bindings`; wrong selections and schema faults stop
before storage. Visible expectation data does not block selected step review,
but metric/expectation evaluation and the meaning of source case success remain
unexamined. Only published 0.2.0 retains the old explicit trace selector; the
current checkout writes trace v2 only and reads old stored bytes unchanged.

The index omits source contents, but **the stored TestRun and invocation remain
private and unredacted**. Exits 0/2/3 and consumed diagnostic IDs keep their existing
meanings; `published_record: null` after a storage failure does not prove absence.
Use `records verify` and `records read-json --name analysis/trace-index.json` after
the original path is gone. For original `source/deepeval.bin` via `read_record_file`,
allow the explicit 4 MiB source budget when the default 1 MiB selected-read limit
is insufficient. This verifies stored bytes, not execution, complete trace coverage
or the truth of source statuses. Published 0.1.1 predates this route; see the
[step-review workflow](AGENT_USAGE.md#inspect-recorded-deepeval-agent-steps).

In a 0.2.0 installation advertising neutral DeepEval binding, use
`records audit-deepeval FILE --bindings PACK.v2.json --root ROOT --id NEW_ID --json`
with an owner-authored `deepeval_metric_bindings.v2` pack and no version flag.
The `deepeval_bound_record.v2` result embeds `evidence_result.v2`: its source
producer version is unknown, its applied reader is an Underwrite rule, and the
pack's raw hash and owner assertion remain separate from the TestRun raw hash.
This route does not verify metric meaning, evaluator configuration or source
authenticity. The owner chooses a v2 declaration once for supported ordinary
occurrences; an old v1 pack is not silently widened.

The composed bound route captures the pack and TestRun independently once,
then saves `source/bindings.bin`, `source/deepeval.bin`, and either
`analysis/evidence_result.json` or `analysis/error.json`, plus `invocation.json`.
Completely captured malformed inputs remain available; incomplete acquisitions
leave no partial source file. Pack acquisition error takes precedence over source
acquisition error. Saved verification checks two base files plus each captured
source. The response keeps the raw pack hash separate from item semantic digests;
stored-byte integrity does not authenticate the owner declaration. Exits 0/2/3
retain the result, diagnostic, and storage-failure meanings above. Saved auto
diagnostics name requested selection, never an unapplied reader. A null returned
publication identity does not prove that the new ID is free.

The current checkout refuses v1 packs and accepts no DeepEval version selector.
A new v2 pack requires the owner's explicit review and reassertion; changing the
old schema label or deleting its version assertion is not a migration. Published
0.2.0 still supports the old route; see [migration](MIGRATION.md). Existing bound
records retain their exact old payloads, pack bytes, source bytes and hashes.

In a 0.2.0 installation advertising automatic Langfuse intake, use
`records audit-langfuse --observations FILE --scores FILE --root ROOT --id NEW_ID --json`.
It accepts a supported pair of blob arrays or official-CLI list envelopes/bodies
without a profile flag and returns `langfuse_record.v2`. The selected reader
family is an Underwrite policy; the actual producer release remains unknown.
It analyzes and retains the same complete input buffers, the result or diagnostic,
and invocation metadata. Both paths are attempted independently; incomplete reads
are never retained as complete sources. Per-input acquisition errors remain in the
output and saved invocation. The entire selected files may contain sensitive source
content, including CLI headers; they are not redacted. Two files are not an atomic
producer snapshot or proof of export completeness.

The automatic v2 record keeps analysis and storage independent: exits
0/2/3 mean result saved, diagnostic saved, or storage failed. Even a diagnostic-only
record consumes the ID. Read `analysis/accounting.json` or `analysis/error.json`
with `records read-json`; `invocation.json` preserves input acquisition details.
Generated analysis is admitted within the selected-read limit before storage.
Large original files can be retained without being readable through that JSON route.
See [the Langfuse workflow](AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations).

The unreleased checkout removes Langfuse `--profile` and Python `profile=`;
published 0.2.0 still retains those historical selectors. Old saved records keep
their original result, invocation and manifest bytes and remain readable.
The automatic v2 route embeds full `langfuse_accounting.v2` or a finite diagnostic;
use `--view row-index` for current `langfuse_row_record.v2` containing only
current row-index v2. It stores `analysis/row-index.json` while retaining both
whole source files. Published 0.2.0 uses the old row-record v1 envelope. Neither
view authenticates the producer, request windows, or completeness. Its private invocation and manifest record `selection_mode:auto`
even for a saved analysis error, but name an applied reader only on success. Recover
`analysis/accounting.json` or `analysis/error.json` by exact logical name after
the originals are removed; run `records verify` for whole-record integrity.
Saved source files may contain HTTP headers, scores, identifiers and observation
I/O. `read-json` has its unchanged 1 MiB selected-input cap; larger retained
source members need the bounded Python `read_record_file(..., max_bytes=...)`
path, while a selected-file read does not replace whole-record verification.
Published `0.1.1` still defaults to the blob `langfuse_record.v1` contract;
0.2.0 intentionally migrates omitted `--profile` to v2.

For an exact supported goppi paired handoff, `review-handoff` can analyze and save
in one call with `--record-root ROOT --record-id ID`; see
[the agent workflow](AGENT_USAGE.md#review-a-completed-paired-handoff-in-one-call).
Its optional capture uses the already-read bytes without reopening source files.
It saves actual error diagnostics if analysis fails, and preserves a computed
analysis in the output if storage fails. Generated invocation records identify
the analysis phase's exit code; the enclosing storage/process outcome is returned
by the CLI envelope, avoiding a self-referential saved result.
The record file budget includes both captured sources and generated documents.
A larger supported handoff may be analyzed without fitting a single record; in
that case requested storage fails before serialization or store mutation, while
the CLI retains the complete analysis. Sources are never silently omitted.

## Retain a Goppi paired trajectory

Underwrite 0.2.0 builds advertising these commands can keep a complete paired+trajectory
export and its analysis in one immutable private record. Published 0.1.1 predates
this route. The initial audit requires an already installed compatible Goppi
offline verifier; reopening does not.

```sh
underwrite records audit-trajectory --exchange /absolute/path/to/export \
  --root PRIVATE_STORE --id trajectory.review-001 --json
underwrite records read-trajectory --root PRIVATE_STORE --id trajectory.review-001 --json
```

The complete outer export must contain `paired/` and `trajectory/`, at a physical
path without symlink components. An optional `--goppi-executable PATH` selects an
existing trusted executable for recording. No evaluation or model is run.

The `goppi.trajectory-review` record contains the seven exact analyzed inputs
under `source/`, `analysis/trajectory.json` or `analysis/error.json`,
`invocation.json`, and `verification/history.json`. Source files are retained in
full and can contain identifying metadata. Use a private store and backup policy.
No source paths are reopened between analysis and storage. Duplicate IDs are
refused rather than overwritten.

The `trajectory_record.v1` response keeps source capture, analysis, native history,
and storage separate. Exit 0 means a result was saved and verified; exit 2 means
a supported diagnostic was saved, or an earlier typed refusal occurred without
publication; exit 3 means storage or stored-byte verification failed. Safely
classified source diagnostics before the native check have history `not_performed`;
semantic diagnostics after a successful check have history `verified`. All verifier
failures, including nonzero exits, produce no record. Failed/incomplete captures,
configuration faults, and output-admission failures also publish no diagnostic
record. A storage failure with `published_record: null` does not prove absence:
inspect the requested ID with `records show` and `records verify` before retrying.

`records read-trajectory` verifies the complete record and binds the result,
source identities, and original verification history to the same manifest. Its
`current_storage_verification` is a new stored-byte check, while
`native_verification_performed_now` is always false. Neither the original export
nor Goppi is needed. Content integrity is not source authentication, independent
verification of a producer's private projection, or a causal explanation.

The dedicated reader accepts the existing 8 MiB trajectory report limit plus a
bounded envelope. Generic `records read-json` remains limited to 1 MiB per selected
file and cannot recover JSONL as one JSON document. To restore any retained source
as verified raw bytes, use its existing member limit:

```python
from pathlib import Path
from underwrite.instrument.ingest.goppi_trajectory import trajectory_member_limits
from underwrite.service.records import read_record_file

name = "paired/rows.jsonl"
source = read_record_file(
    Path("PRIVATE_STORE"), "trajectory.review-001", "source/" + name,
    max_bytes=trajectory_member_limits()[name],
)
raw_bytes = source.data
```

Choose a logical member listed by `records show`; byte recovery does not execute
its contents. Keep recovered source data private.

## Retain a completed goppi baseline

For the exact five-file baseline exchange v2 profile, retain the already
captured source bytes and descriptive `evidence_result.v2` together in the current
checkout (published 0.2.0 emits v1):

```sh
underwrite records audit-baseline --exchange /path/to/exchange \
  --root PRIVATE_STORE --id baseline.review-001 --json
underwrite records verify --root PRIVATE_STORE --id baseline.review-001 --json
underwrite records read-json --root PRIVATE_STORE --id baseline.review-001 \
  --name analysis/evidence_result.json --json
```

The create-only record contains exactly seven files: all five exchange files
under `source/`, `analysis/evidence_result.json` or `analysis/error.json`, and
`invocation.json`. Each source file is the exact buffer used by the analysis;
the command never reopens a source path to store it. The private invocation
contains the fixed command/profile, record ID and analysis state, without a
source/root display path. Current `baseline_record.v2` keeps analysis and storage
separate: exit 0 means the result was saved and verified; exit 2 means a
supported input diagnostic was saved; exit 3 means storage or verification
failed. `verification.files_checked` is seven for a saved record. These are
stored-byte claims, not producer authentication, label truth or approval.

Published 0.2.0 uses the old baseline-record v1 envelope. Current baseline results
keep the software producer version unknown, identify the applied reader and have
no separate declaration pack. Existing records, source bytes and manifest hashes
are not rewritten; read/verify them without retired output schema resources.

The source directory may include private answers and usage data. All five raw
files are retained **only because the caller supplied `--root` and `--id`**;
choose a private POSIX store and backup policy. Aggregate stdout/stderr contains
only bounded hashes, states and finite diagnostics. A capture that fails before
all five bytes are available stores no partial set and leaves the requested ID
available. A complete capture with invalid producer input can save all five
bytes and a finite `analysis/error.json`, consuming the ID. Internal/configuration
or output-admission failures do not label the input malformed and do not
publish a diagnostic record. A null `storage.published_record` after a storage
failure does not prove absence: check `records show` and `records verify` for
the requested ID before deciding whether to retry with a fresh ID.

`records read-json` can return the analysis or other selected JSON at its
unchanged 1 MiB cap. A retained `source/rows.jsonl` can be up to 4 MiB and is
not a `read-json` document. Python callers who need its verified raw bytes can
request the source profile's bound explicitly:

```python
from pathlib import Path
from underwrite.instrument.ingest.goppi_baseline import ROWS_MAX_BYTES
from underwrite.service.records import read_record_file

selected = read_record_file(
    Path("PRIVATE_STORE"), "baseline.review-001", "source/rows.jsonl",
    max_bytes=ROWS_MAX_BYTES,
)
raw_rows = selected.data
```

The selected-file read checks those bytes against their manifest. Use
`records verify` for the whole seven-file record. Neither operation reruns goppi
or changes the original record. This route does not accept the separate C55
operator-answer assessment.

For a selected Promptfoo export, account and retain it without a handwritten
record specification:

```sh
underwrite records audit-promptfoo FILE --root PRIVATE_STORE --id NEW_ID --json
```

Current checkout capture uses SummaryV3 selected-field accounting and returns
`promptfoo_record.v2`. Producer version is a source declaration or unknown, not
a selected reader or a requirement to match package releases. Unconsumed fields
are retained without interpretation. Unsupported summaries and malformed consumed
fields produce finite diagnostics.

The unreleased checkout removes Promptfoo `--version` and Python `version=`.
Published 0.2.0 still supports the old selectors; see [migration](MIGRATION.md).
Existing v1 records remain readable without their old schema resources. For an
old explicit-profile diagnostic, its original metadata/invocation may identify
the selected source profile; the saved error alone does not. Current records
identify automatic selection separately, and a failed selection does not claim
an applied reader. Do not rewrite old metadata or overwrite a record ID. Storage
failure does not promise later recovery; retain the returned independent outcomes.

This explicitly stores the **entire selected file**, the accounting result or
diagnostic, and structured invocation metadata. It analyzes and saves the same
captured bytes. A complete but malformed input is retained; acquisition failure
stores only the diagnostic and invocation. Even a diagnostic record consumes the
new ID. Invocation path fields are canonical display metadata, not byte-exact
argv or a claim that a subprocess ran.

The current Promptfoo record contract returns independent `analysis` and `storage` objects: exit 0
means a result was produced and saved, exit 2 means analysis failed but its
diagnostic was saved, and exit 3 means storage failed (inspect analysis separately).
A count mismatch is a produced result, not approval or an invocation error.
Argument, configuration, or response-capacity failures emit `evidence_error.v1`
on stderr with exit 2 before storing. A failed post-publication verification keeps
its known record identity in `storage.published_record`; null means no identity
was returned, not proof that the requested ID is absent. Never overwrite or
silently retry a reserved ID. Analysis-only `audit-promptfoo` never saves a record.

For manual selection of arbitrary supported evidence, use an explicit spec:

```sh
mkdir -p output
underwrite records save --root output/records-store \
  --spec fixtures/examples/records/record-spec.json --json
underwrite records list --root output/records-store --json
underwrite records show --root output/records-store --id synthetic.capture-001 --json
underwrite records verify --root output/records-store --id synthetic.capture-001 --json
```

The manual example is synthetic. Each `records save` capture needs its own explicit
[`record_spec.v1`](contracts/record_spec.v1.schema.json), unique record ID,
selection, and honest reproduction status. Source paths are relative to the
specification file. They are not persisted; a `source_locator` is recorded only
when explicitly supplied as a nonblank string. Omit an unknown locator; do not
write `"source_locator": null`. The portable
[complete four-file spec example](skills/underwrite-review/references/recording.md#minimal-manual-record-specification)
shows actual input, result, command, and exit files without inventing a locator.
Avoid secrets, credentials, raw thinking streams,
caches, environments, and unrelated working files. Captures copy bytes and leave
the originals in place. A record is not automatically a complete reproduction kit.

```text
STORE/
  .staging/                  temporary manifest publication files
  records/
    RECORD-ID/
      manifest.json          completion marker and selected-file identities
      files/                 self-contained selected bytes
```

New directories use mode 0700 and files use 0600. Existing insecure directories
and symbolic links are rejected rather than repaired silently. The store is
designed for a local POSIX filesystem supporting no-follow reads, directory
fsync, and create-only hard-link publication. It is not a concurrent hostile-user
filesystem sandbox or a distributed store. Choose a private, backed-up location;
`output/` in this repository is ignored by Git and is not a backup.

An ID is reserved exclusively. `manifest.json` is published only after the
selected files have been copied, hashed, and flushed. A crash can leave an
`unpublished` reservation. Such a reservation is not a successful capture and
is never overwritten or automatically reused. Choose a fresh ID after inspecting
the failure. Completed bundles are never changed by these commands. There is no
delete, garbage collection, restore, or automatic retention policy.

`save` and `show` return `record_view.v1`, with the canonical manifest digest
outside the manifest. `list` returns `record_catalog.v1`, distinguishing complete
and unpublished records. `verify` returns `record_verification.v1`: examine
`valid` and `findings`, not just exit status. A valid invocation can return exit 0
with `valid: false` for damaged stored bytes or direct links. Invalid invocations,
invalid target manifests, and storage failures return exit 2 with
`record_error.v1` on stderr and no success payload.

To read a retained JSON result by its manifest logical name, use:

```sh
underwrite records read-json --root STORE --id RECORD_ID --name analysis/accounting.json
```

Find logical names with `records show` when needed. `read-json` always emits JSON;
`--json` is optional. It returns `record_document.v1`, including `content`, the
original file's byte length and hash, and the captured manifest digest. It reads
and verifies that same selected buffer before returning any content. The scope
is **selected_file_against_manifest**: other files and links are not checked.
Use `records verify` separately when whole-record verification is needed. A saved
failure remains a failure inside `content`; exit 0 here only means retrieval worked.

The selected input cap is 1 MiB, JSON depth is 32, and the complete output cap is
2 MiB including the final newline. All JSON root types are supported when admitted
by the existing strict decoder and canonical serializer. Duplicate keys, invalid
UTF-8, non-finite/unsafe numbers, unsupported Unicode, and non-JSON content are
refused. An intact binary file is unsupported content, not a damaged record.
Decimal/exponent tokens use the existing binary64 interpretation; integer tokens
retain the existing integer path. `content` is not the original byte or exact
decimal spelling, and stored strings are not NFC-rewritten.
Unknown logical names, byte mismatches and unsupported content have separate
bounded errors. Reading creates no files and never reopens a producer source path.

Python callers can obtain verified bytes, including non-JSON content, without
knowing the physical store layout. The default is still 1 MiB; an explicit
`max_bytes` may select a larger bound up to the storage-owned 128 MiB file limit.
Callers must choose a bound appropriate to their input contract. This does not
widen the `read-json` command's input or output limits:

```python
from pathlib import Path
from underwrite.service.records import read_record_file

selected = read_record_file(Path("STORE"), "RECORD_ID", "analysis/accounting.json")
raw = selected.data
# selected.sha256 identifies raw; selected.manifest_digest identifies its manifest.
```

Verification checks stored bytes and direct manifest links. It does not rerun
commands or recursively verify linked bundles. `rerun_verification: referenced`
means only that an explicit verification artifact is linked. Its claims remain
claims. `provenance.verified` remains false; an attestation reference or matching
hash does not independently authenticate the producer.

Read, list, and verify check opened private store and member descriptors. Verify
streams each declared member through its checked descriptor, with one extra-byte
probe for growth. These checks do not create an atomic whole-store snapshot or a
hostile-filesystem sandbox: a descriptor opened before a pathname replacement
may still read the admitted original inode.

Bounds are 64 files, 128 MiB per file, 512 MiB per capture, a 256 KiB manifest,
16 direct links, and 1,000 records per catalog. There is no hidden global store.
The root's parent must already exist; read commands never create the supplied
root. Keep separate stores when a
project's retention or population exceeds these bounds.

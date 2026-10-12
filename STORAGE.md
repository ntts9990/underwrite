# Local result records

`records` stores explicitly selected files in a private, immutable local bundle.
It does not discover inputs, execute an evaluation, or certify provenance.
Commands are available in this checkout and wheels built from it; the package
version alone does not establish availability.

For two supported Langfuse exports, use
`records audit-langfuse --observations FILE --scores FILE --root ROOT --id NEW_ID --json`.
It analyzes and retains the same complete input buffers, the result or diagnostic,
and invocation metadata. Both paths are attempted independently; incomplete reads
are never retained as complete sources. Per-input acquisition errors remain in the
output and saved invocation. The entire selected files may contain sensitive source
content; they are not redacted. Two files are not an atomic producer snapshot.

The `langfuse_record.v1` envelope keeps analysis and storage independent: exits
0/2/3 mean result saved, diagnostic saved, or storage failed. Even a diagnostic-only
record consumes the ID. Read `analysis/accounting.json` or `analysis/error.json`
with `records read-json`; `invocation.json` preserves input acquisition details.
Generated analysis is admitted within the selected-read limit before storage.
Large original files can be retained without being readable through that JSON route.
See [the Langfuse workflow](AGENT_USAGE.md#account-for-langfuse-score-kinds-and-associations).

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

For a selected Promptfoo 0.123.0 export, account and retain it without a handwritten
record specification:

```sh
underwrite records audit-promptfoo FILE --root PRIVATE_STORE --id NEW_ID --json
```

This explicitly stores the **entire selected file**, the accounting result or
diagnostic, and structured invocation metadata. It analyzes and saves the same
captured bytes. A complete but malformed input is retained; acquisition failure
stores only the diagnostic and invocation. Even a diagnostic record consumes the
new ID. Invocation path fields are canonical display metadata, not byte-exact
argv or a claim that a subprocess ran.

`promptfoo_record.v1` returns independent `analysis` and `storage` objects: exit 0
means a result was produced and saved, exit 2 means analysis failed but its
diagnostic was saved, and exit 3 means storage failed (inspect analysis separately).
A count mismatch is a produced result, not approval or an invocation error.
Argument, configuration, or response-capacity failures emit `evidence_error.v1`
on stderr with exit 2 before storing. A failed post-publication verification keeps
its known record identity in `storage.published_record`; null means no identity
was returned, not proof that the requested ID is absent. Never overwrite or
silently retry a reserved ID. Analysis-only `audit-promptfoo` remains unchanged.

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
when explicitly supplied. Avoid secrets, credentials, raw thinking streams,
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

Python callers can obtain verified bytes, including non-JSON content within the
same fixed input cap, without knowing the physical store layout:

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

Bounds are 64 files, 128 MiB per file, 512 MiB per capture, a 256 KiB manifest,
16 direct links, and 1,000 records per catalog. There is no hidden global store.
The root's parent must already exist; read commands never create the supplied
root. Keep separate stores when a
project's retention or population exceeds these bounds.

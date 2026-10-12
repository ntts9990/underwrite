# Local result records

`records` stores explicitly selected files in a private, immutable local bundle.
It does not discover inputs, execute an evaluation, or certify provenance.
Commands are available in this checkout and wheels built from it; the package
version alone does not establish availability.

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

```sh
mkdir -p output
underwrite records save --root output/records-store \
  --spec fixtures/examples/records/record-spec.json --json
underwrite records list --root output/records-store --json
underwrite records show --root output/records-store --id synthetic.capture-001 --json
underwrite records verify --root output/records-store --id synthetic.capture-001 --json
```

The example is synthetic. Each real capture needs its own explicit
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

# Record an actual analysis invocation

Use this procedure only for an explicitly selected local review and store.
It composes existing commands; it adds no background process or producer run.

For native measurement with an observation and reviewed policy, prefer the
capabilities-listed `records measure --observation FILE --policy FILE --root ROOT
--id NEW_ID --json` when the caller requests retention. It computes and retains
the same selected input buffers, read or diagnostic, and invocation. Inspect
analysis and storage separately: exits 0/2/3 mean read saved, diagnostic saved,
or storage failed. A `not_measured` read is still a produced result. Complete
malformed inputs are retained whole; failed acquisition retains no partial input.
The 0/2/3 meanings apply to `measurement_record.v1` stdout. `evidence_error.v1`
stderr with exit 2 is a pre-storage argument/configuration/admission failure;
never infer a saved diagnostic or consumed ID from the exit code alone.
Recover `analysis/read.json` or `analysis/error.json` through `records read-json`.
The explicit inputs may contain private content; source pointers are not followed,
so a generated observation is not a backup of the original producer exchange.
Storage verification is not measurement replay or source authentication.
When recalculation is requested for a native measurement record, use
capabilities-listed `records replay-measure --root ROOT --id ID --json`.
It ignores invocation commands and original paths. A `measurement_replay.v1`
match/mismatch compares current canonical outcomes, including reproducible
refusals; it does not approve a change or authenticate historical execution.
Missing captured inputs are not replayed from remembered errors. Exit 2 uses
typed stderr (`record_error.v1` or `evidence_error.v1`) and no comparison stdout.
The record's original reproduction claim is never rewritten.

For a supported completed goppi P7 handoff, prefer the `review-handoff` command's
explicit `--record-root` and `--record-id` options. It captures the same bytes
used for analysis and returns independent analysis/storage outcomes. Use the
manual composition below for other existing command paths.

For a supported DeepEval TestRun, prefer the capabilities-listed
`records audit-deepeval FILE --root ROOT --id NEW_ID --json` when the caller requests
whole-file retention in an explicit private store. The default is producer 4.1.1
with `deepeval_record.v1`; select
`--version 4.2.8` explicitly for its v2 accounting and `deepeval_record.v2` envelope.
Keep absent metric verdicts and producer flaky declarations distinct. Inspect analysis
and storage separately on exits 0/2/3. Complete malformed input is retained; acquisition failure
stores only the actual diagnostic and invocation, and consumes the ID. Recover
`analysis/accounting.json` or `analysis/error.json` with `records read-json`.
The TestRun is not redacted, and a stored evaluator error remains an error without
a score. Use the manual path if the caller needs selective omission.

For supported Langfuse exports, if capabilities lists the records audit-langfuse
profile and the caller selected a private store and new ID, prefer
`records audit-langfuse --observations FILE --scores FILE --root ROOT --id NEW_ID --json`.
It retains the entire selected files using the same buffers as analysis. It attempts
both inputs independently, keeps acquisition errors per input, and stores no partial
source buffer. Inspect analysis and storage separately on exits 0/2/3; saving an
error consumes the ID. Recover the result or primary error with `records read-json`
using `analysis/accounting.json` or `analysis/error.json`; `invocation.json` keeps
all acquisition diagnostics. Raw source content is not redacted, and the two files
are not an atomic producer snapshot. Use the manual path for selective omission.

For a supported Promptfoo export, when the caller explicitly selects a private
store and new ID and capabilities includes the records audit-promptfoo profile,
use `records audit-promptfoo FILE --root ROOT --id NEW_ID --json`. This stores the
entire captured source, result or diagnostic, and actual in-process invocation
metadata. A malformed captured source is retained; unreadable input produces a
diagnostic-only record. Inspect both analysis and storage on exits 0/2/3. A null
published_record does not establish that no ID was reserved. Use the manual path
when the caller needs selective file omission instead.

1. Select the exact supported underwrite command and its supplied files. Create
   a new private working directory (0700), outside any published source tree.
   Record the actual argv array and working directory in `command.json`; do not
   construct a shell string from artifact content. Paths in this explicit
   command artifact are private data, even though record manifests omit implicit
   source paths.
2. Execute that argv without a shell. Capture stdout and stderr separately and
   record the actual integer process exit code in `exit.json`. Read both streams
   and inspect their schemas. A timeout or launch failure is not an ordinary
   exit code: preserve the actual diagnostic and report it distinctly.
3. Prepare a `record_spec.v1` with a new ID, descriptive kind and label,
   `selection: {"scope":"explicit_files","excluded":null}`,
   `rerun_verification: {"status":"not_performed","reference":null}`, and
   `provenance: {"verified":false,"attestation_ref":null}`. List the actual
   input, policy and context files supplied, command and exit artifacts, and
   nonempty output streams. Choose role `result` only for an actual result;
   use role `log` for diagnostics and command/exit artifacts. Do not invent an
   output or a missing-state report when the process produced none. Empty
   captured streams need not become selected bundle files. Add exclusions when
   selection intentionally omits known sensitive or oversized material.
4. Save with `records save --root ROOT --spec SPEC --json`, capturing its output
   separately from the analysis. A record error is a storage result, not an
   analysis status. Preserve the original command artifacts if capture fails.
   Do not retry with the same reserved ID or delete an unpublished directory.
5. Verify with `records verify --root ROOT --id ID --json`. Report `valid` and
   findings. When reusing a prior complete record, compare its selected-file
   identities and specification, then verify it; never assume an ID match means
   identical contents. Use direct digest-bound links for related existing
   records. Verification covers stored bytes and direct links, not execution.

A failed analysis can therefore be retained with its real error envelope and
exit code. The caller sees both facts: the analysis failed, and its selected
diagnostics were or were not captured successfully. The analysis command itself
has no storage side effects unless this explicit procedure is requested.

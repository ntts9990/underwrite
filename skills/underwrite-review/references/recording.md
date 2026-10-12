# Record an actual analysis invocation

Use this procedure only for an explicitly selected local review and store.
It composes existing commands; it adds no background process or producer run.

For a supported completed goppi P7 handoff, prefer the `review-handoff` command's
explicit `--record-root` and `--record-id` options. It captures the same bytes
used for analysis and returns independent analysis/storage outcomes. Use the
manual composition below for other existing command paths.

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

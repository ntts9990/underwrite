# Paired exchange v2 conformance golden

> **Provisional local fixture.** Regenerate and re-pin the schema, golden bytes, hashes, and
> refusal vectors together after the build-identity contract is integrated and the installed
> package path has been verified. This fixture is not an installed-package verification result.

A synthetic public protocol fixture for the paired exchange v2 described in
[Paired export](../../../docs/PAIRED_EXPORT.md#v2-exchange-format). It is not provider evidence, a
benchmark or evidence of user effectiveness.

The fixture represents a controlled native cycle with the capture, verification and local storage
paths exercised without a model call. `same_batch` was evaluated for 12 explore cases, 2 arms and 2
repeats, producing 48 rows and 4 retained trajectory units. The runner then became unavailable, so
`held_in` closed with a partial stop and `held_out` was never run; its reason is
`earlier_stage_partial_stop`, its classification is `not_evaluated`, and its planned case count is
12.

The exchange is reproducible as a checked-in fixture, but a fresh export draws a new assignment
salt and will not reproduce these bytes. `identities.json` records each member's byte count and
SHA-256, both manifest content digests and, because the source is synthetic, its plain
`source_assignment_digest`; with `assignment-salt.json` that digest recomputes the published
commitment. **Never publish a real export's salt or keep a real plain assignment digest beside its
salt.** The salt included here is synthetic and is present only to make this fixture independently
checkable.

`export_build` and each executed `stage_build` use the reproducible `dir_hash` from
[`../build-dir-hash/vector.json`](../build-dir-hash/vector.json)
(`4700ec2d5679e5454bf7eaa5eb59429cc0ff452e736f4828bbc91695c64df7d4`); this synthetic digest is
not the digest of this source checkout.

To verify it, copy `paired/` and `trajectory/` into a new `0700` directory with files set to
`0600`, then run `goppi run verify-paired --input <that directory> --json`. The result has
`data.format: "v2"`, `data.cycle_status: "stopped"`, `data.row_count: 48` and
`data.trajectory.format: "v2"`. Passing only the copied `paired/` checks the bare closure. Keep
`assignment-salt.json` out of the exchange; verification never reads it.

The JSON files are excluded from Biome formatting because their exact bytes are conformance
vectors. `tests/application/paired-exchange-v2.test.ts` verifies the golden and the refusal cases in
[`../paired-exchange-v2-rejections/`](../paired-exchange-v2-rejections/).

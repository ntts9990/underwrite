# Public synthetic Goppi Pi 1.5 trajectory fixture

The seven files in `exchange.tar.gz` are the exact portable export from accepted
Goppi source commit `f88cf07c84ec6f1bcbc36fe8f5474d64acf8906c` (Apache-2.0).
That source repository is currently private, so anonymous readers cannot
independently rebuild this fixture from the generator. The archive and member
hashes are self-contained for consumer regression; producer regeneration needs
an authorized Goppi checkout. Goppi's
`tests/application/live-execution/paired-trajectory-fixture.ts` ran a controlled
native process executor through its Pi 1.5 capture controller, aggregation,
verifier binder, state machine, and local storage under Node 24.20.0. It made no
paid model or provider call. The source generator uses random process IDs, so a
new run is expected to produce different bytes. `pins.json` freezes this run.

The export contains 144 paired rows and 144 retained trajectory records. An
installed Goppi tarball built from that commit returned `valid` with
`content_digest_only` portable authenticity and capture profile
`native_pi_1_5_lifecycle.v1`; an installed Underwrite wheel from local commit
`5323d21` returned `goppi_trajectory_review.v2` with 144 rows. The exact package
hashes and member hashes are in `pins.json` and the local acceptance record.
These observations do not authenticate the source producer independently,
establish verifier correctness, or measure real agent improvement.

The archive has sorted members, zero timestamps, fixed uid/gid and empty owner
names, `0700` directories, `0600` files, and a zero-time gzip header. Its seven
members are checksummed separately in `pins.json`. It excludes raw prompts,
answers, tool arguments/results, credentials, and local absolute paths. Case IDs,
model names, and hashes are still metadata and are not claimed anonymous.

The committed regression checks archive topology, exact bytes, and Underwrite
interpretation with an explicitly **non-verifying** Goppi response stub. It does
not perform native Goppi closure verification in routine CI. The native verifier
was run separately on these bytes using the installed package pair; the ordinary
test must never be described as that verification. No Docker, Goppi dependency,
provider call, or new CI workflow is required to use this fixture.

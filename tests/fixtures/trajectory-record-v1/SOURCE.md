# Frozen trajectory record v1 evidence

These two private-store records were written by Underwrite at commit
`89594225f505ffd4101b16a52b54237ad4f1bc87`, before the trajectory verifier
trust-contract migration. `legacy.v1` uses the public synthetic Goppi trajectory
fixture unchanged. `legacy.diagnostic.v1` copies it except that
`trajectory/trajectory.json` is deliberately replaced with the 13-byte
`{invalid-json` payload, producing `CAPTURE_INVALID_JSON` before verifier
execution. The configured verifier was the repository's explicitly
**non-verifying** protocol stub; no Goppi process or provider was run.

`legacy.v1` contains a result with historical `verified` history.
`legacy.diagnostic.v1` contains a pre-verifier diagnostic with `not_performed`
history. The old `verified` label is historical data, not proof of the stub's
identity or content-closure work. `byte-ledger.json` pins all archive members,
record files, and the six v1 contract byte hashes. Do not regenerate these
records using the new writer.

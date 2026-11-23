# Frozen pre-change trajectory record v2 evidence

This private-store record was written by Underwrite at commit
`4efa4b0d96b448ed88261298fab6849c9e77e93a`, before current reviews began to
disclose cycle status, stage verdict states and runner context. `prechange.v2`
uses the public synthetic Goppi trajectory fixture unchanged. Its stored layout is
`trajectory-record.v2` and its retained analysis is `goppi_trajectory_review.v2`.
The configured verifier was the repository's explicitly **non-verifying**
protocol stub, passed as a Python port; no Goppi process or provider was run.

The `report_accepted` history records only that the stub's protocol-valid report
was accepted. It is not proof of the stub's identity or content-closure work.
`byte-ledger.json` pins the archive, all members and record files, and the byte
hashes of the two retained v2 contracts the record needs. The archive has sorted
members, zero timestamps, fixed uid/gid and empty owner names, `0700`
directories, `0600` files, and a zero-time gzip header. Do not regenerate this
record using the new writer: a newer analysis of the same source is a new record.

# Synthetic Goppi C55 common-result fixture

The five exchange members in this directory were originally captured from Goppi
commit `0da07b7a2b032a345b0bd9aa3ec526f3c4d15663`, directory
`fixtures/conformance/operator-answer-exchange/`. They are byte-for-byte
identical at accepted Goppi main commit
[`ff037e517830dec3a311ac652a52cd3ca3ad0140`](https://github.com/ntts9990/goppi/commit/ff037e517830dec3a311ac652a52cd3ca3ad0140),
recorded in `pins.json`. [PR #21](https://github.com/ntts9990/goppi/pull/21)
merged that source and [main CI](https://github.com/ntts9990/goppi/actions/runs/39090275261)
passed its automatic Linux quality job; manual packed and Docker jobs were
skipped. `pins.json` records each member's SHA-256 and byte length. The files
are public synthetic examples; no live answer, integrity key, or private
verifier material is included. The copied `ume1-schema.json` is an Apache-2.0
Underwrite schema already present in this repository. The Goppi source README
describes its attribution and test scope.

Underwrite's gate checks the selected common-result file against its installed
schema and item invariants, the source assessment byte hash, and the seven ordered
C55-to-common-result meanings in this fixture. The fixture's manifest is retained
as a pinned source member, but this gate does not verify its content digest,
permissions, source authentication, task correctness, or deployment authority.
Goppi's own portable verifier owns bundle closure checks. These source claims
remain descriptive and require human review. This fixture does not establish
registry publication, producer identity, or a trajectory companion contract.

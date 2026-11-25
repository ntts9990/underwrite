# Goppi execution.v2 conformance fixtures

These exact bytes were imported from the final Goppi PR #342 handoff. The source handoff, artifact checksum manifest, and producer tarball SHA-256 are pinned in `pins.json`. The golden execution is synthetic: its `4700...` build digest is the published directory-hash vector, not the digest of the installed Goppi tarball. Build and split declarations remain producer claims.

`paired-exchange-v2/` contains the normal paired and trajectory exchange. `paired-exchange-v2-rejections/` contains final native-verifier refusal vectors. `build-dir-hash/vector.json` fixes the independent directory-hash algorithm. The synthetic assignment salt is only for fixture recomputation and must not be treated as a real export secret.

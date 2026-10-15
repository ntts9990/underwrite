# Changelog

## 0.1.0

- Both `underwrite` and `underwrite-core` are `0.1.0`. The app requires
  `underwrite-core>=0.1.0,<0.2`; an older installed core cannot satisfy it.
- Since the published `0.0.1` artifacts, the supported local CLI and Python paths
  include version-selected producer evidence accounting and inspection, explicit
  unknowns and next evidence checks, optional private records, and separately
  scoped native binary-event measurement and acceptance classification.
- The `0.1.0` code refuses a crafted pin-manifest path that could overwrite a
  writable file outside the registry in published `0.0.1`. See the README's
  older-installation caution; this is not a claim about every filesystem risk.
- These paths do not authenticate producer claims, measure arbitrary external
  scores, analyze agent trajectories, or authorize merges or deployment. Consult
  `underwrite capabilities --json` on the installed artifact for exact support.

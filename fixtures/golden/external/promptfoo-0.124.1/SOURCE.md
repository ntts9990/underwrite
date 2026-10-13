# Promptfoo 0.124.1 sanitized synthetic export

`results.json` is a sanitized derivative of an actual complete Promptfoo 0.124.1
summary-v3 JSON export. The pinned npm CLI evaluated seven deterministic local
provider cases; its intentional failed/error rows produced exit 100 and a valid
export. No model or external provider was invoked. This fixture does not establish
independent provenance, user adoption, execution completeness or approval.

Source package: `promptfoo@0.124.1`, MIT, npm package integrity
`sha512-Y6fq/WGTL7lPvDsirNSmmmcPLhURwRao03iv8fCoLbFq6H1eqg5VVTmXiap5Jl+fiRaQQZXYMHb2N1gQm1tGyQ==`.
The capture used Node 26.7.0; the package declares Node >=22.22.0.

The private unchanged export SHA-256 is
`0617705885cb9e13edbf55a6586aa7e6a27a438b21b6d4cf0f151287a261dd40`.
The public derivative SHA-256 is
`17348c05994cdef96e90c7bd24b8ab46f0f5cd5629ad27a6f5203c51205152c7`.
Hashes identify bytes, not authenticated origin. `observations.json` binds the
included derivative, selected codec and recipe files; it does not describe the
private raw export as the included file.

## Exact sanitization

Only four string values changed after parsing the complete original JSON:

- `/config/providers/0`: local provider path becomes `./provider.cjs`.
- `/config/outputPath/0`: export path becomes `./synthetic-results.json`.
- `/config/basePath`: working directory becomes `.`.
- `/results/results/5/error`: the temporary installation prefix in stack locations
  becomes `/synthetic/promptfoo-install`; the error message, package-relative
  stack paths, function names and line coordinates remain unchanged.

A local recursive comparison checked unchanged keys, array order, types and every
other value. All strings were scanned for residual local locators and credential
patterns. The raw export, logs, installation paths and environment records are
kept outside the published fixture. JSON formatting was rewritten; this is not an
unchanged vendor export.

The source stats and row flags remain three successes, one assertion failure and
three errors. The numeric 0.3 assertion retains its true source verdict. The 2.5
cost component is a synthetic provider declaration, not money spent or an
independently measured cost. The missing-cost row still lacks `response.cost`
while its separate row `cost` is zero; that zero is not evidence of free execution.

## Reproduction recipe and limits

`provider.cjs` and `promptfooconfig.json` describe the same synthetic cases using
portable relative paths. `no-network.cjs` is the capture's Node preload guard.
These files are a reproduction recipe, not the original invocation or an exact
historical byte-reproduction promise. Historical IDs, timestamps and environment
strings will differ. They are not run by repository tests.

For a separately prepared exact 0.124.1 installation, use a disposable copy of this
folder and a clean allowlisted environment without credentials. Set task-local
configuration/cache directories, disable telemetry and update checks, set
`C35_NETWORK_LOG` to a disposable log path, and preload the guard before the CLI:

```sh
node --require ./no-network.cjs /path/to/pinned/promptfoo/dist/src/entrypoint.js \
  eval --config ./promptfooconfig.json --output ./reproduced-results.json \
  --no-cache --no-progress-bar --max-concurrency 1
```

The original guarded evaluation recorded blocked attempts from loader setup and
a telemetry-disabled notice; it did not observe zero attempted networking. No
external generation or telemetry traffic passed those guarded entry points. This
is workload-level evidence, not an OS network-sandbox guarantee. Installation was
a separate network phase. No producer rerun is needed to test this fixture.

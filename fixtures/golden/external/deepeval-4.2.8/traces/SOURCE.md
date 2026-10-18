# Native DeepEval 4.2.8 trace examples

These are unchanged JSON files written by `deepeval==4.2.8`, through
`EvaluationDataset.evals_iterator`, nested `@observe` callbacks and
`DisplayConfig(results_folder=...)`. Only the filenames were shortened. All
inputs, outputs, errors and fixed metrics are synthetic; no model was called.
The original package's relevant source files matched immutable upstream commit
[`b16e7c06c3539667ad933a3cca9690d20d3222e4`](https://github.com/confident-ai/deepeval/tree/b16e7c06c3539667ad933a3cca9690d20d3222e4).

| File | Workload | Original byte SHA-256 |
| --- | --- | --- |
| `success.json` | One agent, lookup and verification tools, fixed trace/tool metrics | `317a13bd5b33fa43e2fbe3e2d4a0e0c882bfffa9cd01e662355cee0d511ee8bd` |
| `recovery.json` | One agent, a deliberately errored lookup and a successful fallback; successful case and trace | `fc97a1a35b10e528267c02c1a32af18de8a84fad89cee518f7de2b368d7807cd` |

These are different workloads, not an aligned before/after experiment. The source
script establishes its intended fallback; parent UUIDs alone do not establish
retries, causality or improvement. Fixed metric success is an authored declaration,
not measured model quality. Hashes identify bytes, not independent provenance.

## Reproduce with the producer

`capture_success.py` and `capture_recovery.py` are formatted copies of the original recipes,
with identical parsed Python syntax trees.
Install `deepeval==4.2.8` in a separate tool environment, not Underwrite's runtime.
Run either recipe with that environment's Python and a new, existing empty output
directory. The original process used an allowlisted environment without inherited
credentials, with task-local home/cache directories and these settings:

```sh
env -i PATH=/usr/bin:/bin HOME="$PROBE_HOME" LC_ALL=en_US.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 \
  DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 \
  DEEPEVAL_UPDATE_WARNING_OPT_IN=0 DEEPEVAL_NO_INSPECT_PROMPT=1 \
  DEEPEVAL_LOCAL_STORE=json DEEPEVAL_CACHE_FOLDER="$PROBE_CACHE" \
  CONFIDENT_TRACE_VERBOSE=0 CONFIDENT_TRACE_FLUSH=0 \
  /absolute/path/to/tool-env/bin/python \
  fixtures/golden/external/deepeval-4.2.8/traces/capture_recovery.py "$NEW_EMPTY_OUTPUT_DIR"
```

Each script checks the producer version and installs a Python audit hook before
importing DeepEval to reject socket connect, address lookup and sendto events.
Both original runs recorded zero such attempts. This is evidence about those
audited events, not an OS network sandbox. No service credentials, Docker or paid
provider were used. Generated UUIDs, times and durations can differ on recapture;
the recipe does not promise byte-identical regeneration.

## Review recorded steps

From a development checkout advertising `deepeval.test-run|4.2.8|trace-index` in
`underwrite capabilities --json`:

```sh
uv run --frozen --no-sync underwrite audit-deepeval \
  fixtures/golden/external/deepeval-4.2.8/traces/recovery.json \
  --version 4.2.8 --view trace-index --json
```

Published `underwrite==0.1.1` predates this route. The selected index reports
observed source statuses and within-trace parent references without copying raw
names, UUIDs, prompts, arguments or error text. It does not evaluate metrics,
certify execution, prove instrumentation completeness or align different runs.
The ordinary metric accountant still refuses populated traces; do not delete
source branches to force acceptance. Keep real exports private.

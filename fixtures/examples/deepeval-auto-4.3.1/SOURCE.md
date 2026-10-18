# Native DeepEval 4.3.1 automatic-intake examples

These are unchanged JSON bytes written by `deepeval==4.3.1` using public
`evaluate` / `EvaluationDataset.evals_iterator`, nested `@observe` callbacks
and `DisplayConfig(results_folder=...)`. Only filenames were shortened. All
case content, fixed metrics and errors are synthetic; no model was called.
The isolated CPython 3.12.13 installation's nine relevant source files matched
immutable upstream commit
[`29dcf5f24397715a60348c1e41e7d416a3b2d6c8`](https://github.com/confident-ai/deepeval/tree/29dcf5f24397715a60348c1e41e7d416a3b2d6c8).

| File | Workload | Original byte SHA-256 |
| --- | --- | --- |
| `ordinary.json` | One case; positive and zero scores plus evaluator error | `7c08d852bc0660810ae6387b7eb858b11675dc9e8ff58d6a975e787134a31316` |
| `recovery.json` | Successful case and trace, with one deliberately errored tool and successful fallback | `b9a90b7a6af901d6ba27d9dcb7afafb88117bcf075b3aa098aeeb31e33ec7894` |

Neither file declares a producer package version. The capture context establishes
which tool wrote these examples; an arbitrary same-shaped file does not establish
its producer release or authenticity. Underwrite's automatic result therefore
keeps producer version unknown. These examples are not registered as 4.2.8
goldens and do not require a low-level 4.3.1 codec or a CLI version selector.

The two workloads are not an aligned before/after experiment. A successful source
case beside an errored tool is observable, not proof of recovery, causality or
improvement. Fixed metrics do not measure model quality. Original source hashes
identify bytes, not independent provenance.

## Capture recipes and limits

`capture_ordinary.py` and `capture_recovery.py` are formatted copies of the actual
recipes with identical parsed Python syntax trees. Install `deepeval==4.3.1` in a
separate tool environment, not Underwrite's runtime. Run a recipe with that
environment's Python and a new, existing empty output directory. The original
process used an allowlisted environment without inherited credentials, with local
home/cache directories and these opt-outs:

```sh
env -i PATH=/usr/bin:/bin HOME="$PROBE_HOME" LC_ALL=en_US.UTF-8 \
  PYTHONDONTWRITEBYTECODE=1 DEEPEVAL_TELEMETRY_OPT_OUT=1 \
  DEEPEVAL_DISABLE_DOTENV=1 DEEPEVAL_DISABLE_LEGACY_KEYFILE=1 \
  DEEPEVAL_UPDATE_WARNING_OPT_IN=0 DEEPEVAL_NO_INSPECT_PROMPT=1 \
  DEEPEVAL_LOCAL_STORE=json DEEPEVAL_CACHE_FOLDER="$PROBE_CACHE" \
  CONFIDENT_TRACE_VERBOSE=0 CONFIDENT_TRACE_FLUSH=0 \
  /absolute/path/to/tool-env/bin/python \
  fixtures/examples/deepeval-auto-4.3.1/capture_recovery.py "$NEW_EMPTY_OUTPUT_DIR"
```

Both recipes install a Python audit hook before importing DeepEval to reject
socket connect, address lookup and sendto events. Both original runs recorded
zero such attempts. This checks those Python events; it is not an OS network
sandbox. No service credentials, Docker or paid provider were used. Generated
UUIDs, timestamps and durations can change on recapture.

Neither native capture exercises `expectationsData` or `datasetVersion`.
DeepEval's expectation evaluator needs an additional judge path that was not run.
Synthetic marker mutations test Underwrite's ordinary-accounting refusal and
trace-view limitations; they are not native expectation exports or proof of
support for every 4.3.1 branch. `datasetVersion` means a dataset version, not a
producer package version. These files support the selected ordinary/step reader
contracts, not a promise to understand every future producer change.

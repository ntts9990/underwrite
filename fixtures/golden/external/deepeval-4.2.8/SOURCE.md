# DeepEval 4.2.8 ordinary TestRun captures

These four files are unchanged JSON bytes written by `deepeval==4.2.8` through
its public `evaluate()` API on Python 3.12.13. Only the filenames were shortened.
Every input, answer and metric is synthetic. Unlike the older sanitized examples,
these captures were not edited after export. Hashes establish local byte identity,
not independent provenance or the truth of source judgments.

| File | Workload |
| --- | --- |
| ordinary.json | Fixed positive and zero scores, plus a deterministic evaluator error |
| flaky_metric.json | Failed flaky metric and a passing non-flaky sibling |
| flaky_case.json | Failed source case designated flaky |
| score_without_verdict.json | Score-only metric with no threshold/verdict and a passing sibling |

The adjacent `sample_eval.py` is a refactored reproduction of the deterministic
capture recipe; the exported JSON bytes above remain unchanged. It also checks
`assert_test()` separately: an ordinary failure raises, while the two flaky
examples do not. The source case in `flaky_case.json` nevertheless has
`success=false` and contributes to `testFailed`. `source_flaky` is a producer
declaration, not measured nondeterminism or a predicted CI result.

The score-only row has a score, no error and no success/threshold fields. Its
aggregate `metricsScores` entry labels it differently; underwrite accounts for
rows and explicitly does not validate those producer aggregate summaries.

## Reproduction

Install the exact producer in an isolated environment, outside application
dependencies. Run `sample_eval.py OUTPUT_DIRECTORY` with that environment's
Python and a new, existing empty output directory. The script checks the package
version before evaluation and refuses network connect, address lookup and sendto
events before importing the producer. It uses no model clients. The original run
recorded zero such events; this is not a general network sandbox guarantee.

The capture process used an allowlisted environment with no inherited credentials:
`DEEPEVAL_TELEMETRY_OPT_OUT=1`, `DEEPEVAL_DISABLE_DOTENV=1`,
`DEEPEVAL_DISABLE_LEGACY_KEYFILE=1`, `DEEPEVAL_UPDATE_WARNING_OPT_IN=0`,
`DEEPEVAL_NO_INSPECT_PROMPT=1`, `DEEPEVAL_LOCAL_STORE=json`, and a task-local
`DEEPEVAL_CACHE_FOLDER`. The remaining variables supplied the isolated interpreter's
PATH, a UTF-8 locale and `PYTHONDONTWRITEBYTECODE=1`. See the
[official privacy configuration](https://deepeval.com/docs/data-privacy) and the
pinned package's `config/settings.py`. A fresh
run may change identifiers, times or durations; it need not recreate byte hashes.

Pinned reference: [python-v4.2.8 TestRun model](https://github.com/confident-ai/deepeval/blob/python-v4.2.8/deepeval/test_run/api.py)
and [MetricData model](https://github.com/confident-ai/deepeval/blob/python-v4.2.8/deepeval/tracing/api.py).
The profile is deliberately limited to the demonstrated ordinary TestRun fields;
it does not claim support for new case token metadata, expectations, classifiers,
or nonempty conversational/trace accounting.

`observations.json` records each source selector, raw hash, normalized observation
digest, and recipe hash. Test mutations derived from these files are separate
inputs; they are not claimed to be unchanged producer exports.

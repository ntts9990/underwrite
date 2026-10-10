# Local evidence-review comparison

This is a bounded engineering experiment with synthetic evaluation tasks:
constructed fixtures plus a locally retained archived producer export. It is not
a user study, producer bug report, or integration certification. Real-user review time,
repeat use, market demand, and additional semantic value remain **not measured**.

## Same-question comparison

The question was whether declared Promptfoo 0.123.0 total and category counts
agree with the rows in the supplied file. Both methods received the same raw
bytes and declared selector. The [jq comparator](fixtures/examples/evidence/counts-baseline.jq)
checks required counters and row flags before counting, and refuses a different
selector. It is a task-scoped baseline, not a full producer schema validator.
Underwrite additionally runs its bounded artifact/manifest and codec checks.
No claims about equivalent total validation coverage are made.

The four cases were total mismatch, equal totals with category mismatch,
missing stats, and an explicitly unsupported selector. Both methods answered
all four registered questions correctly. Basic count reconciliation is available
with jq; this experiment demonstrates no additional semantic insight for those
questions. Neither method establishes a planned population, execution truth,
infrastructure cause, or permission to merge.

An initial expected category delta incorrectly assumed which row the total
mismatch fixture omitted. The initial warmup was stopped; inspection of the raw
flags showed one success and one error against declared 1/1/1, so the failure
delta is -1. The expected answer was corrected before rerunning both methods.
This was a study-oracle correction, not an underwrite defect.

One local run used Darwin arm64, Python 3.12.13, jq 1.7.1-apple,
and uv 0.12.5 against underwrite commit `3785940`. Each command had one warmup
and three timed subprocess invocations, alternating tool order. Timings include
`uv run` startup for underwrite and reflect different amounts of validation.
They are milliseconds of **command wall time**, not human review time, general
performance estimates, or an acceptance threshold.

| Case | Exit: jq / underwrite | jq median (range), ms | underwrite median (range), ms |
| --- | --- | --- | --- |
| `total-mismatch` | 0 / 0 | 2.87 (2.76–3.22) | 77.11 (76.29–77.79) |
| `category-mismatch` | 0 / 0 | 2.92 (2.83–3.29) | 74.53 (74.48–76.55) |
| `missing-stats` | 5 / 2 | 3.09 (2.71–3.56) | 74.34 (72.76–74.83) |
| `unsupported-selector` | 5 / 2 | 3.18 (2.74–3.66) | 70.78 (69.68–71.78) |

jq was faster in this small run. Typed output and diagnostic consistency remain
workflow features to test with real users; they are not measured time savings.
Producer UIs and CTRF were not exercised in this run.

Reproduce a count question from a prepared checkout:

```sh
jq --arg format promptfoo.eval-output --arg version 0.123.0 \
  -f fixtures/examples/evidence/counts-baseline.jq \
  fixtures/examples/evidence/synthetic-promptfoo-category-mismatch.json
uv run --frozen --no-sync underwrite audit-counts \
  fixtures/examples/evidence/synthetic-promptfoo-category-mismatch.case.json --json
```

The total-mismatch and missing-stats cases use their correspondingly named
fixtures. For the unsupported control, create a new temporary directory, copy
the category fixture bytes and its case manifest, and set only the manifest's
`source.format_version` to `0.122.0`; give jq the same `--version 0.122.0`.
Do not edit an original artifact or treat refusal as a successful evaluation.

## Archived producer capture

A second, user-requested round used an archived Promptfoo 0.123.0 echo export.
Its authored capture record describes unchanged producer output, and its recorded
raw digest matches the retained bytes. That verifies identity against the record,
not independent attestation of the capture history. The task itself is synthetic;
this is distinct from both a hand-constructed fixture and real-user adoption.
The archived original remains private; its ignored local copy is not tracked
or packaged in this repository.

The unchanged export contains three rows, with source-declared counts of two
successes, one assertion failure, and zero errors. Both tools independently
recomputed those counts. A separate derived copy removed the assertion-failure
row while retaining counters. Both tools detected failure delta -1 and total
delta -1. The mutation is intentional and is not an upstream bug. Neither method
can determine whether an unmarked real-world mismatch came from deletion,
producer behavior, or another cause.

The same one-warmup/three-invocation command-wall protocol was used after the
diagnostic follow-up; both methods answered both questions correctly.

| Case | Exit: jq / underwrite | jq median (range), ms | underwrite median (range), ms |
| --- | --- | --- | --- |
| results | 0 / 0 | 3.25 (3.15–3.55) | 80.77 (80.44–82.47) |
| derived-missing-row | 0 / 0 | 3.52 (3.04–3.55) | 80.10 (79.55–80.22) |

This supports a small existing-producer compatibility check. It does not establish
broader format support, additional semantic insight over jq, or human time savings.
A public sanitized derivative and its production recipe are documented in
[the agent guide](AGENT_USAGE.md#local-promptfoo-export-ingest-then-stop-at-the-measurement-boundary);
that derivative is not the unchanged archived input used in this round.

## Consumer requirements for a future producer export

These are requirements of the current underwrite consumer, not a claim that
any other product exports them. An adapter remains out of scope until a
producer-owned, authorized export and a concrete user task exist.

| Consumer route | Required evidence | What a summary cannot supply by implication |
| --- | --- | --- |
| Count audit | Exact supported Promptfoo profile and rows with declared counters | Another producer's aggregate categories are not automatically Promptfoo categories. |
| Declaration comparison | Two supported accounting reports plus their canonical digests and explicit condition/metric declarations | Digests and a threshold do not reveal the underlying model, prompt, evaluator, units, or denominator. |
| Paired binary evidence | Declared primary cases, planned case/arm/repeat slots, outcomes or missing reasons, contexts, outcome definition, and assumptions | Aggregate successes/missing counts do not identify pairs, affected cases, or which repeat was absent. |

The constructed [aligned](fixtures/examples/evidence/synthetic-summary-aligned.json)
and [crossed](fixtures/examples/evidence/synthetic-summary-crossed.json) inputs
have identical binary outcome totals within each arm. With the same policy they
produce the same n=4 and effect=0, but different paired intervals: `[0,0]` and
`[-1,1]` in the reference run. Even per-arm totals do not determine pairing.
Neither interval proves equivalence or authorizes a change. The fixture regression
checks the relationship, rather than pinning those interval endpoints.

```sh
uv run --frozen --no-sync underwrite pair-binary \
  --input fixtures/examples/evidence/synthetic-summary-aligned.json \
  --policy fixtures/examples/evidence/paired-binary-policy.json --json
# Repeat with synthetic-summary-crossed.json.
```

## Observed friction and bounded follow-up

The comparison exposed a diagnostic loss: `audit-counts` replaced an existing
codec reason for malformed input with generic `INVALID_ARTIFACT`. The other
concrete consumer gap was independent of count speed: an observed outcome could
not carry `source_reason`, although a missing slot could. Changing observed zero
to missing just to retain that reason would corrupt the evidence.

The follow-up preserves trusted codec diagnostics and supports unverified source
reason annotations on observed outcomes without classifying by their text.
Reasons cannot change the outcome, missingness, primary population, or estimate.
These are unreleased pilot changes; published package artifacts and both package
versions remain unchanged. They do not add a producer adapter or establish
real-user value. The next external validation needs a user-authorized artifact,
its actual review question, and observed work with a credible comparator.

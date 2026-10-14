# UME-1 contract vectors

These JSON files are **hand-authored expected results for contract review**.
`audit-baseline` emits `evidence_result.v1` for the pinned goppi exchange, and
`audit-deepeval --version 4.2.8 --bindings PACK` emits it for DeepEval metrics
using a separately supplied, owner-asserted `metric_definition.v1` pack.
Neither path makes source claims independent truth. A conforming adapter must
derive each item from its source, validate the result, and independently
establish every check it claims. Schema validation alone is not verification.

| Vector | Source and fact fixed by the vector |
| --- | --- |
| `deepeval-4.2.8.evidence_result.v1.json` | Unchanged tool-generated synthetic `fixtures/golden/external/deepeval-4.2.8/ordinary.json`: one case, three metric occurrences; score 0 and evaluator error are different occurrences. The planned occurrence population is unknown. |
| `promptfoo-0.124.1.evidence_result.v1.json` | Sanitized derivative of a tool-generated synthetic `fixtures/golden/external/promptfoo-0.124.1/results.json`: seven rows, with source scores and errors; planned rows beyond this export are unknown. Row 6 has score 0.3 and source `success=true`, not an independently verified pass. |
| `goppi-synthetic-v2.evidence_result.v1.json` | The public synthetic five-file `fixtures/examples/baseline-event/exchange`: four planned cases and four source row records; those rows contain three observed binary outcomes and one missing outcome. Observed false is not missing. This is a baseline exchange v2 example, **not** the developing C55 operator-answer profile. |
| `metric-definition-example.v1.json` | A synthetic latency declaration. Its semantic digest is `underwrite_core.canonical.content_digest(semantic)`; method and policy are separate and null in the example. The declaration does not implement a timer or authorize a gate. |

The result's `population.unit` is source-profile-specific. `observed_count`
counts parsed source records, including records with error or missing outcomes;
it is not the count of successful measurements. Three DeepEval metric
occurrences, seven Promptfoo rows, and four goppi case rows are not a common
denominator. `planned_count: null` means no planned inventory was provided; it
is not zero. A source score or status remains `origin: source_claim` and
`permitted_effect: descriptive` when its metric meaning is unbound. A hash
checks bytes only. A bundle lists each member hash and a manifest hash; their
binding requires a separately evidenced manifest check. Bundle member roles are
unique object keys after a strict duplicate-key JSON decode.

`verification[].evidence_ref` values in these examples are local test labels,
not portable retrieval locations or self-authenticating receipts. A consumer
must resolve each label to a trusted check and reperform it before claiming that
scope was verified. Defining a portable evidence-reference binding belongs to a
future adapter/interop milestone, not to this schema-only release.

If there is no input or the format cannot be identified, a non-analyzed result
uses `source: null`, `population: null`, and no interpreted items. If bytes exist
but the selected format/version is unsupported, the source identity can still be
reported while processing remains `unsupported` and items stay empty.

The fixtures contain no private goppi run. A completed 21-case goppi v2 exchange
was previously inspected locally and remains outside Git. This public vector
tests the same false-versus-missing distinction without copying private bytes,
case IDs, paths, answers, or labels. The all-not-applicable score-1 example in
the test is a **synthetic counterexample**, not a captured JevEval export.

JSON Schema checks structure and several state combinations. A producer or
consumer must additionally recompute semantic digests, validate bundle member
bytes and manifest binding, resolve bound definitions, check coordinate
uniqueness and count arithmetic, and establish any claimed independent review.
Neither contract adds a universal score, `read.v2` input, stable goppi verdict,
acceptance decision, or deployment authority.

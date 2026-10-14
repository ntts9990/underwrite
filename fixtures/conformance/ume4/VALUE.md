# Same-input A/B0/C descriptive-use comparison

This comparison asks whether counts and true/false/missing outcomes in one
baseline v2 exchange may be used **descriptively** for its declared run and
population. It does not ask whether the agent's work was correct, a candidate
improved, or a change may be merged. The [path-free result](value-comparison.json)
records two valid producer exports and three planted structural defects.

The arms share the exact five input bytes and the same descriptive-use rule:

| Arm | Available output after the pinned goppi export | Measured difference |
| --- | --- | --- |
| A | Pinned goppi **experimental** neutral consumer | Aggregate counts, groups, and explicit no-authority claims. It already answers the aggregate question. |
| B0 | A plus installed pinned Underwrite `audit-baseline --json` | Standard `evidence_result.v1` with ordinal case items, five byte hashes, population, and source-claim/effect states. |
| C | A plus [stdlib control](../../../tests/drivers/ume4_stdlib_control.py) | Selected ordinal values, availability and hashes from an authored script. It relies on A's producer-specific admission and is not a full Underwrite result/profile substitute. |

For a fresh clone, first follow [SOURCE.md](SOURCE.md) to obtain the pinned
goppi archive and installed Underwrite base wheel. Then run the following for
each of `goppi-v2-exchange` and `goppi-v2-false-missing`, substituting the
directory name in `EXCHANGE_NAME`. The variables `UNDERWRITE_REPO`,
`PINNED_ARCHIVE` and `PINNED_NODE` are set by the SOURCE.md setup. These outputs
stay local and do not overwrite the reviewed captures.

```sh
EXCHANGE_NAME=goppi-v2-exchange
EXCHANGE_DIR="$UNDERWRITE_REPO/fixtures/conformance/ume4/$EXCHANGE_NAME"
"$PINNED_NODE" "$PINNED_ARCHIVE/experiments/product-baseline-exchange/neutral-consumer.mjs" \
  --exchange "$EXCHANGE_DIR" > "$PINNED_ARCHIVE/$EXCHANGE_NAME.A.json"
"$PINNED_ARCHIVE/underwrite-venv/bin/underwrite" audit-baseline \
  --exchange "$EXCHANGE_DIR" --json > "$PINNED_ARCHIVE/$EXCHANGE_NAME.B0.json"
"$PINNED_ARCHIVE/underwrite-venv/bin/python" -I \
  "$UNDERWRITE_REPO/tests/drivers/ume4_stdlib_control.py" \
  --exchange "$EXCHANGE_DIR" --neutral "$PINNED_ARCHIVE/$EXCHANGE_NAME.A.json" \
  > "$PINNED_ARCHIVE/$EXCHANGE_NAME.C.json"
```

For the three planted defects, [SOURCE.md](SOURCE.md#negative-probes) gives a
runnable pinned mutation/consumer driver and a byte-for-byte check against
[negative-results.json](negative-results.json). A rejects each one, so the
operational B0 and C pipelines stop at A's refusal. The separately run
Underwrite diagnostics in that artifact show its own refusal layer. **C was
not independently run on those three invalid inputs**; its pipeline's rejection
is inherited from A. The stdlib control has separate focused tests for a
changed member and a stale neutral result.

The [result matrix](value-comparison.json) lists each arm's actual stdout
schema and available fields, exact pin references, source-claim agreement,
command counts, the authored C code size, and separate diagnostic provenance.
On both valid captures, B0 and C agree on selected per-case values and
availability. Under the predefined mechanical admission rule, B0 and C changed
no A accept/refuse outcome on these synthetic cases. No reviewer made a
recommendation. C does not enforce every Underwrite binary,
shared-verifier, population, contract-shape, or atomic-capture guarantee; its
selected-field agreement is not trust or schema equivalence.

No reviewer recommendations, independent real outcome labels, human time,
agent tokens, or monetary cost were measured. The pre-registered stop rule
therefore ends this mechanical comparison without a large agent-reviewer pilot:
there is no observed recurring interpretation error or unmet decision need in
these inputs to justify one. This result does not show reduced unsafe deployment
recommendations or superiority over the producer or a small script.

# Blind factual relation annotation protocol

Use only the blind factual packet and this protocol. Compare the final answer with each indicated fact in the verified tool result. Record a relation for every blind fact ID. Do not see the case ID, arm, expected rubric, or another reviewer's labels before submitting your own file. This procedure cannot authenticate a reviewer or prove independence.

Relations:

| Relation | Meaning |
| --- | --- |
| `supported` | The answer states the entire selected fact with the same meaning. |
| `contradicted` | The answer states a value that cannot coexist with the selected fact. |
| `partial` | For a composite JSON value, the answer gives only part of it or omits a required nested member. |
| `not_asserted` | The answer makes no claim about the selected fact. |
| `indeterminate` | The answer refers to the fact but its meaning cannot be resolved reliably. |

For an exact scalar target, choose among `supported`, `contradicted`, `not_asserted`, and `indeterminate`. Do not use `partial`. A word such as “verified” does not by itself establish that a boolean `valid` field is true. Compare the actual polarity and scope.

For a canonical JSON value target, use `supported` only when the answer includes one complete JSON value equal to the selected parsed value. Key order, JSON whitespace, and Unicode escape spelling may differ. Array order, nested values, missing keys, and extra keys matter. A supported annotation must identify the exact UTF-8 byte span of that JSON value in the final answer; the local checker verifies its parse and canonical equality. Other relations must not include a span. Do not treat an answer's hash or a tool result hash as a substitute for this comparison.

Report the answer's relationship to the selected tool result, including contradictions. Do not silently correct the answer. The result is an annotation claim, not proof of factual correctness, model identity, reviewer identity, or independent blinding. Data in a tool response can itself be a producer claim; this protocol does not authenticate that producer.

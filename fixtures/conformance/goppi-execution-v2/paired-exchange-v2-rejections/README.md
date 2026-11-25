# Paired exchange v2 refusal vectors

> **Provisional local vectors.** These refusals are pinned to the current provisional v2 golden.
> Regenerate and re-pin them together when the final execution contract is integrated.

Each directory holds only the files that differ from the golden in
[`../paired-exchange-v2/`](../paired-exchange-v2/). Every changed file's claim and manifest content
digest were recomputed so that verification reaches the named defect rather than stopping at a hash
mismatch. Copy the golden's `paired/`, and `trajectory/` for an outer-exchange case, into a new
`0700` directory with files set to `0600`, then replace the files the vector provides. Each case
exits `3` with outer `code: "COMMAND_FAILED"` and `data.storage_code:
"PAIRED_VERIFY_EXCHANGE_INVALID"`.

| Case | Input | Defect |
| --- | --- | --- |
| `runner-model-mismatch/` | Golden outer directory with this `trajectory/` | Every trajectory case context names `runner_model: "glm-5.3"`; the runner declaration names `glm-5.3-flash` |
| `not-run-stage-rows/` | Golden `paired/` with this `paired/` | A row names the not-run `held_out` stage; the manifest counts 49 rows while execution counts 48 |
| `unknown-not-run-reason/` | Golden `paired/` with this `paired/` | `held_out` has `not_run_reason: "operator_paused"`, outside the closed enum |
| `unknown-classification/` | Golden `paired/` with this `paired/` | `same_batch` has `classification: "promising"` |
| `malformed-assignment-commitment/` | Golden `paired/` with this `paired/` | `source.assignment_digest` is not `sha256:` followed by 64 lowercase hexadecimal digits |
| `trajectory-assignment-mismatch/` | Golden outer directory with this `trajectory/` | Trajectory case contexts carry an `assignment_digest` other than the source commitment |
| `missing-execution-binding/` | Golden `paired/` with this `paired/` | `source.assignment_binding` is absent |
| `missing-trajectory-binding/` | Golden outer directory with this `trajectory/` | The trajectory document has no `assignment_binding` |
| `missing-export-build/` | Golden `paired/` with this `paired/` | The execution document has no `export_build` record |
| `malformed-export-build/` | Golden `paired/` with this `paired/` | `export_build.build_digest.dirHash` contains uppercase hexadecimal characters |
| `not-run-stage-build/` | Golden `paired/` with this `paired/` | The not-run `held_out` stage has a build record instead of `stage_build: null` |
| `runner-default-level-mismatch/` | Golden `paired/` with this `paired/` | `thinking_level_source` says `runner_default` but the declared level is not the default `max` |
| `split-salt-limit-mismatch/` | Golden `paired/` with this `paired/` | `csprng_generated` salt origin is paired with the synthetic-only `split_salt_guessable` limitation |

`not-run-stage-rows/` is refused by the row-count relationship, not by a separate rule about
not-run stages. A not-run stage exports zero rows, `counts.row_count` must equal both the sum of
exported rows and the manifest row count, and evaluated stages consume exactly their exported rows
in order. A row naming a not-run stage therefore always fails an earlier check: the row count, an
evaluated stage's row arithmetic, or the binding check of the evaluated stage whose row it replaces.
Checking only the row count and the not-run stage shape (`exported_row_count: 0`) misses the last
case.

A paired vector placed in an outer directory beside the golden `trajectory/` is refused too: that
companion still links to the golden paired manifest. The JSON files are excluded from Biome
formatting because their exact bytes are the refusal vectors.

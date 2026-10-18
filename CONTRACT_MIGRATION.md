# Current contract consolidation milestone

Status: implementation in progress; unreleased development changes. Published
0.2.0 remains unchanged. [Milestone #2](https://github.com/ntts9990/underwrite/milestone/2)
covers the entire repository. Package versions and publication are outside this
milestone. A reviewed plan is not completed implementation.

Initial inventory: 93 authored schemas, all assigned a decision below, plus
non-schema identities. Four new envelopes are required only where the selected
input or embedded result changes an existing public contract.

## Target architecture and what “v2 consolidation” means

The final product has one current owned computation/output path per semantic
family; users do not select obsolete Underwrite generations through producer
version/profile flags. Current default inputs flow through audit, record, compare
and inspection consistently. Current generators no longer read retired schemas
as templates. Old computation engines and old-generation writing paths are
deleted, not hidden behind aliases or compatibility switches.

The target is **not** a string search result with no `v1` text. Independent
single-generation contracts, mathematical method identities, native/external wire
formats and immutable hash domains have different owners and lifecycles. Renaming
those without a changed contract creates new identities and migration work while
leaving exactly the same architecture. Their retained status is individually
recorded and verified, not treated as an unexplained exception. Current
`measurement_policy.v3`, `deepeval_accounting.v3`, and `deepeval_record.v3` retain
their true identities: the existing v2 contracts mean different, obsolete things.

Pure computations retain domain boundaries and existing shared owners; effectful
adapters own capture/schema admission. Native execution/export remain producer
responsibilities. There is no new registry framework, generic migrator, background
service, report renderer, or dependency. The temporary ledger is execution evidence,
not a second runtime source of truth.

## Binding architectural decisions

1. **Current schemas independently own their shape.** Invert old-template
   dependencies before removing old runtime paths. A generator-only slice preserves
   exact existing generated current bytes and their ordering. Use shared fragments
   only for genuinely shared field semantics. Do not copy an old schema verbatim
   into a differently named template or have a generator read its own output.
2. **Remove public legacy output selectors.** Retire `audit-promptfoo --version`,
   producer audit/capture DeepEval `--version`, and Langfuse `--profile` routes that
   select obsolete outputs. New default paths remain current and admit supported
   old raw source shapes. Low-level `ingest/project` producer-wire selectors remain
   separate and explicit. Unknown/removed CLI options fail with the existing typed
   usage error, empty stdout, and exit semantics; no silent ignore or fallback.
3. **Python retirement is real.** Remove obsolete public keyword parameters,
   legacy functions and old-generation branches, including `assemble_read` and
   `legacy_measurement_validators`. A removed keyword naturally raises `TypeError`
   and a removed import fails; document these exact breaking changes and current
   replacements. Do not maintain a second legacy engine merely to avoid these
   normal API removal signals. Current input errors retain their established domain
   diagnostics. References to legacy JSON schemas may remain solely for a finite
   migration refusal before any computation.
4. **Promptfoo comparison consumes current accounting v2.** Keep
   `declared_comparison_case.v1`/`declared_comparison.v1`: these are current,
   independent contracts. Reject accounting v1 instead of converting it. Canonical
   report digests, explicit conditions/metric assertions, unknowns, and false
   verification flags remain unchanged.
5. **DeepEval keeps current accounting/record v3.** No-flaky/flaky selected-shape
   branches and trace mechanics remain, because they express different real source
   meanings. Retire package-version→output-generation dispatch. New binding uses
   the v2 pack and v2 common result exclusively; owner reassertion is required to
   broaden a previously version-scoped v1 pack. Never drop its `format_version`
   silently and pretend the assertion was originally broader.
6. **Langfuse preserves two shape engines under one current interface.** Array/blob
   and CLI-list data have different metadata and association rules. Keep those
   pure owners; remove strict historical output envelopes/adapters. Introduce
   `langfuse_row_record.v2` embedding only `langfuse_row_index.v2`. Retire the active
   mixed `langfuse_row_record.v1` writer and its build/resource dependency; old row
   records remain byte-readable through generic storage.
7. **One inspector output, truthful legacy input.** Introduce
   `common_result_inspection.v3` and `common_result_inspection_actions.v3` as sole
   current inspector outputs. Their `selected_result.format` explicitly admits
   `evidence_result.v1|v2`; `raw_hash` always hashes the original selected file.
   Keep exact-schema input dispatch and the matching trusted input validator.
   There is no runtime upgrade/relabel of the input. Retire output writers and
   packaged schemas for inspection/action v1 and v2. Existing v2's constant input
   format cannot truthfully represent v1; changing it in place would change a
   published contract, hence the actual next generation v3. Derive action/binding
   vocabulary from the selected input owner, not an independent enum copy.
8. **Retain `evidence_result.v1` only at demonstrated external read boundaries.**
   Goppi C55/UME fixtures/pins genuinely use it. Keep its frozen installed validator
   for inspector admission and external conformance, and original source bytes.
   Remove own DeepEval and Goppi baseline v1 result writers. Baseline emits v2 with
   a truthful applied reader, unknown software producer version unless actually
   declared, and `declaration_source=null` when none was supplied. Introduce
   `baseline_record.v2` embedding current evidence-result v2 and retire its v1 writer;
   do not mutate the old published aggregate's embedded const. Native wire
   version v2 is not a software producer version. Never rewrite Goppi fixtures or
   refresh their pins to simulate migration.
9. **Storage manifests and identity protocols remain current v1.** There is no
   competing writer to eliminate in `result_record.v1`, chains, seals, canonical
   hashing, receipts or generic record views. Keep their exact identity and one
   writer. This avoids artificial v2-manifest conversion and historical link repair.
   Retained producer payloads can carry old schema strings as data; that does not
   revive a retired computation. Generic history read/verify must work with retired
   producer schemas absent from the installed package.
10. **Current measurement is directional event measurement only.** Preserve policy
    v3/read v2/claims v2/decision v2. Extract shared current input/numeric helpers
    before deleting legacy read orchestration and old-only jury/availability/
    stratification composition. Preserve calibration/e-process arithmetic still
    used by current logic. Unknown assumptions, missing data and incomplete
    populations must not improve merely because an old engine disappears.
11. **Single-generation domains are audited, retained, not relabeled.** Binary run
    inventory/audit, paired binary input/policy/evidence, paired context/review/
    handoff, release inspection, trajectory review/offline-verifier contract and
    current error/configuration envelopes each get an explicit no-competing-writer
    review. They describe distinct evidence constructs, not failed migrations.
12. **No data mutation migration.** New analysis of old raw bytes is a fresh result
    or record ID with current policy. Old manifest/document/pack/chain hashes remain
    as recorded. If required source/owner assertions are unavailable, return an
    explicit unsupported/migration-needed state; do not manufacture a v2 result.

## Dependency-ordered execution slices

Each slice includes implementation, focused verification, a separate refactoring
pass, independent review and authoritative preflight before commit/push. A verified
commit/PR is a checkpoint; the active whole goal continues to the next slice.

### S1 — Current-builder ownership inversion (first executable checkpoint)

Start with Promptfoo, then acceptance, on disjoint files if parallel authors help.
Promptfoo current counting/row fragments and current builders stop reading v1
authored outputs; acceptance v2 stops loading claims/decision v1. Preserve exact
current generated bytes. Temporarily retained old writers may use shared current
field owners until S2/S7 retires them. No new product behavior in this checkpoint.

Owned surfaces: `scripts/build_promptfoo_compat.py`, old Promptfoo row/record
builders as necessary, minimal same-meaning schema fragment owner, and
`scripts/build_acceptance_v2.py`; existing generator contract tests. Prove current
generation in a temporary root where legacy template files genuinely do not exist,
not merely by scanning source text. Existing valid/invalid contract vectors stay
valid/invalid. Neither package manifest nor active runtime retirement is premature.

### S2 — Complete Promptfoo current workflow

Make `audit-promptfoo`, its Python byte/file/row APIs, `records audit-promptfoo`,
`audit-counts/audit_case`, and declaration comparison use the current v2 family.
Keep manifest expected-hash/contained-path/same-capture checks. Reuse inspection
acquisition/projection ownership; never inspect then reopen the original file.
Remove explicit old-output selector branches across CLI/Python. Update capabilities,
help, migration, synthetic example analyses and their actual canonical digest
bindings. Delete retired active schemas/generators/resource links/includes only
after all their consumers are gone. Old raw producer captures remain untouched.

Evidence: default exported JSON→audit→record→comparison works installed outside
checkout; missing/unknown/different declarations remain honest; wrong digest,
bad path/input and removed selector fail without output or storage. Historical
Promptfoo records reopen/verify unchanged without old schemas packaged.

### S3 — Langfuse current views and capture

Invert four old schema-template dependencies into current per-shape builders,
preserving current v2 accounting/row bytes. Keep actual array/blob and CLI semantic
engines. Remove strict profile dispatch and public old output adapters. Introduce
row-record v2 as decided, retain current accounting-record v2, remove retired
writers/resources. Use actual loopback CLI capture fixtures as source evidence;
no live server, export or authentication setup is necessary.

Evidence: both matching shape pairs succeed, mixed families refuse, numeric zero/
boolean false/text/category and missing associations stay distinct, captured bytes
and partial acquisition failures are retained honestly, current output installed
schemas match, historical blob/CLI/row records remain readable.

### S4 — DeepEval current analysis and meaning packs

Invert old accounting/trace/body and record template dependencies. Retire old
package-version choices and pure legacy result projection functions while retaining
shared classification rules and current shape-specific branches. Current account/
record v3, trace/trace-record v2, binding-pack/bound-record/result v2 are sole new
flows. V2 pack definitions derive directly from the metric-definition owner. Retire
old bound-record builder transformation; compose current identities directly.

Evidence: old and newer supported raw TestRun fixtures produce current contracts;
no-flaky/flaky mismatch, unsupported expectations, absent verdict/threshold,
observed zero, metric errors and empty populations retain meanings. V1 pack gets
a finite refusal; documented owner reassertion creates a new v2 pack/hash.
Capture analysis/pack identities match retained bytes; installed API/CLI agree.

### S5 — Common-result and native baseline integration

Decouple current evidence-result and inspector schemas from old templates. Retain
frozen evidence-result v1 solely as external input validation; remove own v1
emission in `goppi_baseline.py`, audit/record adapters and composed baseline schema.
Create baseline-record v2 for the changed nested result, retiring baseline-record
v1 output rather than redefining it. Existing retained baseline records stay intact.
Implement one new v3 inspector/actions output, keeping original input format and
raw hash. V1 and v2 input processing must share pure invariants/histogram owners.
Do not imply structure checks reperform verification or establish provenance.

Evidence: actual C55 pinned v1 source remains byte-identical and inspectable with
new v3 output; current DeepEval v2 result also yields that output; exact unknown/
future schema refusal persists. Current baseline raw exchange→v2 result→record→
inspection works offline. Replacing field labels in a pinned v1 result is not a
test fixture migration. Existing v2 consumers must follow the documented changed
inspector contract, not an aliased old contract.

### S6 — Retire legacy native measurement engine

Map current imports, separate shared `read_input` checks/slots and current numeric
primitives, then delete old `measurement.read::assemble_read`, old-only orchestration
and `cli.measure::legacy_measurement_validators`. Delete obsolete policy/read/
claims/decision/seed runtime resources after S1 makes current builders independent.
Keep explicit old policy/claim refusal codes where they help migration; old JSON
must never activate old computation.

Evidence: current prepare-policy→measure→record→replay→accept installed flow retains
semantic/policy/read/claim digest binding and outcomes. No observation, incomplete
roster, unavailable prediction, unknown timing/assumptions and both directional
tails retain correct states. Shared Brier/ECE/pass-k/e-process edge behavior remains
covered through current owners. Actual old records reopen but incompatible replay
refuses honestly rather than invoking a removed evaluator.

### S7 — All remaining domains and interface closure

Verify retained single-current families in the ledger against implementation:
binary run/paired/handoff; release linkage; trajectory audit/capture/history/read;
ingest observation; acceptance candidate; finite errors; utility/catalog/config;
core integrity and storage. Reuse existing tests when implementation is unchanged;
do not add superficial suffix gates or rerun unrelated mutation campaigns. If this
review reveals another active obsolete path, remove it through the same bounded
loop and update its ledger evidence. Preserve testable no-second-writer decisions.

In affected slices update `main.py` descriptors, Python docs, `AGENT_USAGE.md`,
`STORAGE.md`, agent skill/recording reference, and current-development migration
guidance. Keep released 0.2.0/0.1.1 claims as history. The cleanup changes supported
APIs without changing package metadata; mark checkout differences explicitly.

### S8 — Installed artifact and complete-goal audit

Join initial 93-row ledger plus new rows against actual schema/resource/runtime
state. Every retired schema has no current producer/consumer/generator prerequisite
except expressly listed historical bytes; evidence-result v1 has only exact external
read duties. Every kept family has one current writer/meaning owner and no hidden
old selector. Resolve dynamic validator paths, not only textual references.

Verify actual wheel and sdist populations/resources, installed current workflows,
generic old-record reads with retired resources absent, direct/whole manifest
binding and tamper refusal, no native provider use during readback, Python parity
and removed API behavior. Run final full preflight after last edit and separate
independent review. Preserve protected PR gates and exact delivered commit evidence.
No release or package bump follows. Goal completes only after all rows and all
slice acceptance criteria have authoritative evidence or an explicitly accepted
retention rationale verified against current state.

## Cross-cutting regression and storage rules

- Acquire input once through existing bounded descriptor owners; never replace
  canonical object binding with raw-file hashing or vice versa. Original raw hashes
  stay constant across new analysis; changed result documents get changed digests.
- Schema resource failures remain configuration failures, not invalid-user-input
  success or a fallback to a more permissive historical reader.
- `not_measured`, `indeterminate`, `abstained`, error, missing and observed zero stay
  distinct. Source claims, owner assertions, byte integrity, current verification
  and historical verification never collapse.
- New record writers do not overwrite IDs. Historic result-record manifests, links,
  invocation, source files, analysis and verification history are immutable. Generic
  `records read-json`/verify must not require retired producer schemas or tools.
- Use real published 0.1.1 and 0.2.0 history evidence; a report whose schema string
  was manually changed is not a historical fixture. Mutation testing must target
  Underwrite-owned temporary copies, never source stores or Goppi fixtures.
- Generator inversion tests require missing old template files, exact regenerated
  current bytes, and representative semantic negative cases. Resource retirement
  tests inspect actual built distributions, not only package configuration.
- Follow dependency ordering: input owner→current analysis schema→current capture
  schema→current CLI/capabilities→packaged assets→installed workflow. Avoid unrelated
  full reruns on unchanged commits; final preflight remains authoritative.

## Alternatives and tradeoffs

**Literal every-v1→v2 renaming** would break chain/seal/semantic digests, external
standards, pinned producer fixtures and published current v2/v3 meanings while
preserving old branches structurally. Rejected.

**Keep all historical execution branches** minimizes API breakage but preserves
the exact maintenance/selection problem the user asked to remove. Rejected.

**Current-only input everywhere** would allow inspector v2 to remain unchanged,
but drops real accepted Goppi C55 v1 input and historical interoperability. Rejected
in favor of one next inspector contract over honest external-format identities.

**Selected architecture** accepts documented development API breaks and two new
inspection v3 schemas plus Langfuse row-record v2 and baseline-record v2. It reduces active generations
without claiming incompatible things share one schema; stable identity protocols
remain unchanged. Cost: migration guidance and exact cold/history boundaries must
remain explicit. Benefit: one maintained computation/producer path per owned
family and useful interoperable input without hidden downgrade engines.

## Non-schema protocol ledger

| Identity / group | Decision and evidence requirement |
|---|---|
| `underwrite-canonical-json/v1` | Retain exact serialization identity/bytes; no alternative algorithm branch introduced. Core canonical vectors and dependent digest bindings remain unchanged. |
| `chain_row.v1`, `anchor.v1`, `seal_record.v1` | Retain immutable chain/seal identities and one writer/verifier; old hash values/linkage are not reissued. |
| `https://in-toto.io/Statement/v1` | Retain external standard URI verbatim; Underwrite has no authority to rename it. |
| `https://underwrite.dev/receipt/v1` | Retain current predicate; changing it is a new signed/canonical statement protocol with no cleanup benefit. |
| `pins.v1` | Retain single current manifest parser/refresh writer; no sibling refresh or new hashes during cleanup. |
| `underwrite.evidence-bundle.v1` and codec selector | Retain current native wire protocol used by current measurement; not legacy read.v1. Raw retained bundles remain exact. |
| `binary-event-directional.v1` | Retain current method profile backing policy v3/read v2; do not confuse method generation with output generation. |
| `underwrite.measurement_semantics.v1` | Retain digest domain; otherwise semantics→policy→claims→candidate→read identity would change for no semantic reason. |
| `underwrite.regression_case.v1` | Retain case-ID hash namespace and stable split assignment; no relabel/repartition of old cases. |
| Five current diagnostic method v1 labels | Retain observed-event-fraction, uniform-subset-all-success, Brier MSE, fixed-width gap, beta-mixture directional method identities and numerical coverage. |
| DeepEval rule-profile v1/v2 and reader policy IDs | Preserve actual no-flaky/flaky interpretation identities used by current v3; retire only package-version/output selection. |
| Langfuse blob/CLI rule-profile IDs, Promptfoo SummaryV3 and selected-reader IDs | Preserve semantic source-shape/reader identities, not fake producer release claims. |
| External Promptfoo/DeepEval/Langfuse/Inspect/OpenInference/OTLP selectors | Preserve supported exact source wire profiles and fixture provenance; ordinary audits stop requiring them, low-level ingest stays explicit. |
| Goppi P7/baseline/trajectory/C55 wire and verifier identifiers | Preserve accepted pinned source identities; Goppi read-only. Own projected current result changes separately. |
| UME/conformance fixture identities and historical pins | Preserve evidence, distinguish from current runtime writers; no repinning to conceal behavior changes. |
| `preflight_result.v1`, local inventory/continuity schema labels | Retain current tool diagnostics; not evaluation output generations and not new product contracts. |
| Metric definition `version`, evaluator/policy identifiers | Caller-owned meaning identities; never globally change strings ending v1/v2. |

## Completion evidence per schema

The table appended below is an exact initial-schema population check. A decision
is not implementation evidence. The delivery record must attach actual changed paths, focused/
installed check results, current commit and review disposition before marking each
row complete. `KEEP` rows need verified single-current ownership and relevant
existing regression evidence; they do not require unnecessary edits. `RETIRE`
rows need current-path replacement plus absence from active APIs/build prerequisites
and deployed resources. `EXTERNAL_READ` requires an exact closed input boundary,
no own legacy writer, and a verified historical/pinned artifact. New output rows
need their full schema/CLI/API/installed/migration acceptance.

| Initial schema | Decision | Slice | Required meaning / evidence |
|---|---|---|---|
| `acceptance_decision.v1` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `acceptance_decision.v2` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `acceptance_error.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `artifact_case.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `artifact_inspection.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `artifact_inspection_error.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `baseline_record.v1` | RETIRE | S5 | New baseline_record.v2 embeds evidence_result.v2; published old nested v1 contract is not mutated. |
| `binary_run_audit.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `binary_run_inventory.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `boundary_allowlist.v1` | KEEP_CONFIG | S7 | One current configuration format, no competing engine; preserve expiry/unknown rules. |
| `bundle_index.v1` | KEEP_INTEGRITY | S7 | Single current integrity protocol; byte/digest and external receipt linkage unchanged. |
| `capabilities.v1` | KEEP_CURRENT_ENVELOPE | S7 | Single current transport/error/catalog result, distinct semantics; no cosmetic rename. |
| `change_candidate.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `cli_result.v1` | KEEP_CURRENT_ENVELOPE | S7 | Single current transport/error/catalog result, distinct semantics; no cosmetic rename. |
| `common_result_inspection.v1` | RETIRE | S5 | One new v3 inspector output truthfully labels original v1/v2 input; no schema relabeling. |
| `common_result_inspection.v2` | RETIRE | S5 | One new v3 inspector output truthfully labels original v1/v2 input; no schema relabeling. |
| `common_result_inspection_actions.v1` | RETIRE | S5 | One new v3 inspector output truthfully labels original v1/v2 input; no schema relabeling. |
| `common_result_inspection_actions.v2` | RETIRE | S5 | One new v3 inspector output truthfully labels original v1/v2 input; no schema relabeling. |
| `declared_claims.v1` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `declared_claims.v2` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `declared_comparison.v1` | KEEP_CURRENT_SEMANTIC | S2/S4 | Independent single current declaration/meaning owner, not producer accounting generation. |
| `declared_comparison_case.v1` | KEEP_CURRENT_SEMANTIC | S2/S4 | Independent single current declaration/meaning owner, not producer accounting generation. |
| `deepeval_accounting.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_accounting.v2` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_accounting.v3` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `deepeval_bound_record.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_bound_record.v2` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `deepeval_metric_bindings.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_metric_bindings.v2` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `deepeval_record.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_record.v2` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_record.v3` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `deepeval_trace_index.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_trace_index.v2` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `deepeval_trace_record.v1` | RETIRE | S4 | Remove old emitters/pack scope/selectors; current v3 or v2 preserves true meanings. |
| `deepeval_trace_record.v2` | KEEP_CURRENT | S4 | Keep current identity; do not downgrade v3 or widen owner assertions silently. |
| `evidence_error.v1` | KEEP_CURRENT_ENVELOPE | S7 | Single current transport/error/catalog result, distinct semantics; no cosmetic rename. |
| `evidence_result.v1` | EXTERNAL_READ | S4/S5 | Frozen exact external C55/UME input validator only; remove all own legacy writers and generator-template use. |
| `evidence_result.v2` | KEEP_CURRENT | S4/S5 | Sole own common-result writer; source/reader/declaration identities remain separate. |
| `exemptions.v1` | KEEP_CONFIG | S7 | One current configuration format, no competing engine; preserve expiry/unknown rules. |
| `gate_rules.v1` | KEEP_CONFIG | S7 | One current configuration format, no competing engine; preserve expiry/unknown rules. |
| `goppi_trajectory_review.v1` | KEEP_TRAJECTORY | S7 | Single current report/history/read workflow; retain seven-file binding, full limit, historical verification distinction. |
| `goppi_trajectory_verifier_response.v1` | KEEP_EXTERNAL_VALIDATOR | S7 | Trusted consumed-field validator of Goppi command_result.v1/paired verification v2; not an old own writer. |
| `instrument_error.v1` | KEEP_CURRENT_ENVELOPE | S7 | Single current transport/error/catalog result, distinct semantics; no cosmetic rename. |
| `langfuse_accounting.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_accounting.v2` | KEEP_CURRENT | S3 | One current envelope with real blob/CLI shape distinctions retained. |
| `langfuse_cli_accounting.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_cli_record.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_cli_row_index.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_record.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_record.v2` | KEEP_CURRENT | S3 | One current envelope with real blob/CLI shape distinctions retained. |
| `langfuse_row_index.v1` | RETIRE | S3 | Current v2 preserves blob/CLI shape engines; retire historical envelopes and profile paths. |
| `langfuse_row_index.v2` | KEEP_CURRENT | S3 | One current envelope with real blob/CLI shape distinctions retained. |
| `langfuse_row_record.v1` | RETIRE | S3 | New v2 envelope embeds only current row output; published old mixed union remains historical bytes. |
| `measurement_error.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `measurement_policy.v1` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `measurement_policy.v2` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `measurement_policy.v3` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `measurement_record.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `measurement_replay.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `measurement_semantics.v1` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `metric_definition.v1` | KEEP_CURRENT_SEMANTIC | S2/S4 | Independent single current declaration/meaning owner, not producer accounting generation. |
| `observation.v1` | KEEP_CURRENT_ENVELOPE | S7 | Single current transport/error/catalog result, distinct semantics; no cosmetic rename. |
| `paired_binary_evidence.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `paired_binary_input.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `paired_binary_policy.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `paired_handoff_review.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `paired_review.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `paired_review_context.v1` | KEEP_CURRENT_EVIDENCE | S7 | One current descriptive/paired construct; P7 binds exact input/policy v1 digests, no alternate engine to retire. |
| `promptfoo_accounting.v1` | RETIRE | S1/S2 | Current v2 owns whole Promptfoo workflow; history remains original data. |
| `promptfoo_accounting.v2` | KEEP_CURRENT | S1/S2 | Single current writer independent of retired schema generation; no fake producer version. |
| `promptfoo_record.v1` | RETIRE | S1/S2 | Current v2 owns whole Promptfoo workflow; history remains original data. |
| `promptfoo_record.v2` | KEEP_CURRENT | S1/S2 | Single current writer independent of retired schema generation; no fake producer version. |
| `promptfoo_row_index.v1` | RETIRE | S1/S2 | Current v2 owns whole Promptfoo workflow; history remains original data. |
| `promptfoo_row_index.v2` | KEEP_CURRENT | S1/S2 | Single current writer independent of retired schema generation; no fake producer version. |
| `read.v1` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `read.v2` | KEEP_CURRENT_NATIVE | S1/S6/S7 | Current event/acceptance path; own generation and digest domain; no obsolete alternative writer. |
| `receipt.v1` | KEEP_INTEGRITY | S7 | Single current integrity protocol; byte/digest and external receipt linkage unchanged. |
| `record_catalog.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `record_document.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `record_error.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `record_spec.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `record_verification.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `record_view.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `release_case.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `release_inspection.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `release_inspection_error.v1` | KEEP_CURRENT_LINKAGE | S2/S7 | Single bounded artifact/release linkage workflow; codec version and source identity not new accounting generation. |
| `result_record.v1` | KEEP_STORAGE | S7/S8 | Single current request/manifest/read/error protocol; preserve immutable identity and linked historical reads. |
| `statistical_seed.v1` | RETIRE | S1/S6 | Superseded native engine/resource; current helper extraction and current builder independence first. |
| `term_registry.v1` | KEEP_CONFIG | S7 | One current configuration format, no competing engine; preserve expiry/unknown rules. |
| `trajectory_record.v1` | KEEP_TRAJECTORY | S7 | Single current report/history/read workflow; retain seven-file binding, full limit, historical verification distinction. |
| `trajectory_record_read.v1` | KEEP_TRAJECTORY | S7 | Single current report/history/read workflow; retain seven-file binding, full limit, historical verification distinction. |
| `trajectory_verification_history.v1` | KEEP_TRAJECTORY | S7 | Single current report/history/read workflow; retain seven-file binding, full limit, historical verification distinction. |

New schema decisions (not hidden in initial denominator):

| New schema | Slice | Acceptance |
|---|---|---|
| `langfuse_row_record.v2` | S3 | Sole current row writer, current row_index.v2 embedded, old v1 union never rewritten. |
| `baseline_record.v2` | S5 | Sole current baseline writer embeds evidence_result.v2; old nested-result identity and stored history remain intact. |
| `common_result_inspection.v3` | S5 | Sole summary output, exact selected input v1/v2 plus original raw hash; no upgraded input fiction. |
| `common_result_inspection_actions.v3` | S5 | Sole actions output, source-owned vocabularies and same identity guarantee as summary. |

Ledger set check: 93 initial names matched the initial tracked contract inventory exactly; no duplicates, missing names or added initial names. This is planning coverage only.

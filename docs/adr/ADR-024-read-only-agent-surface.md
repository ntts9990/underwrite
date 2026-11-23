# ADR-024: Allow a read-only local stdio MCP server over CLI result contracts

Status: accepted and implemented for the bounded read-only stdio surface.

Coding agents already call `underwrite` through the documented CLI, the importable
Python API, and the `underwrite-review` skill. The owner has asked for a Model
Context Protocol (MCP) surface as well. `AGENTS.md` previously said not to expand
the product into "Docker, a server, or a web interface". That rule exists to keep
Underwrite a local evidence tool: no network listener, hosted service, background
process, or second product with its own semantics.

MCP calls the tool provider a "server" even when it is a child process speaking
over stdin/stdout. Such a process can stay inside the rule's intent if it is
another projection of the same results. Underwrite therefore narrows the rule
instead of removing it:

- The only permitted server is a **local stdio** MCP server that the calling
  client starts and stops. No network listener, HTTP transport, socket, daemon,
  hosted service, telemetry, or web interface is added.
- The server is **read-only**. A tool that writes local records or has any other
  effect needs a separate decision and an explicit start-time flag. Tool
  annotations declare effects, but they are hints to the client, not enforcement.
- Every tool returns the **same result contract** as the corresponding CLI JSON
  output, from the same owner; the text content is serialized the same way as CLI
  stdout. The server does not compute
  verdicts, error meanings, limits, or next actions of its own. Where a CLI command
  lacks a suitable machine contract or a needed shape (for example a bounded result
  on overflow), that contract is extended first, with the existing limit codes such
  as `OUTPUT_LIMIT_EXCEEDED`, and the CLI uses it too.
- Measurement comes before tools: model-visible bytes of the tool list and
  descriptions get a budget and a shrink-only ratchet, and each description states
  when to use and when not to use the tool. The gate lands in the same change as
  the first tools, with initial ceilings equal to the measured values.
- Untrusted strings from inputs (paths, names, free text) are handled in two ways.
  The text content and CLI JSON escape control and format characters (Unicode Cc,
  Cf, Zl, Zp). Most emitters use `ensure_ascii=True`; bounded retained JSON
  escapes those categories after canonical serialization. The stdio adapter
  rejects any remaining raw characters. Parsing text yields structured content. Structured content
  is parsed by the client, so serialization escaping does not protect it there:
  fields an agent acts on (status, verdict, reason codes, summaries, next actions,
  errors) carry only Underwrite-owned vocabulary and grammar-checked identifiers,
  and input strings appear only in bounded data fields described as data. A client
  that renders those data fields raw remains unprotected; this is stated, not
  claimed solved. Existing action fields that already carry input strings are
  listed first and moved by a feature slice with its own migration, not silently.
  Input validation failures are tool execution errors.
- Tool names use the `underwrite_` prefix so the server can sit beside other MCP
  servers in one client without collisions.

A new runtime dependency (for example the official MCP Python SDK) is not accepted
by this ADR. The implementing slice compares it with a standard-library stdio
JSON-RPC implementation on protocol conformance, maintenance cost, install
footprint, and pure-layer isolation, and records the result before adding anything.

## First-tool implementation decision (2026-11-16)

For one no-input `underwrite_capabilities` tool, choose a standard-library JSON-RPC
stdio adapter. It adds no dependency and calls the existing CLI result path. The
official Python SDK's [low-level Server](https://py.sdk.modelcontextprotocol.io/advanced/low-level-server/)
would handle more transport and lifecycle machinery, but its low-level tool
handler still needs explicit argument validation. Its installation also brings
additional runtime packages, and its [v2 migration](https://py.sdk.modelcontextprotocol.io/migration/)
shows an API change that would need version-specific maintenance. Here the
smaller dependency-free adapter is justified only with real-process transcript
tests for the [2025-11-25 stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
[lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle),
[ping](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/ping),
and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools).
If more protocol revisions, resources, prompts, or client interoperability are
needed, compare the then-current SDK again and pin its exact version before adoption.

The entrypoint ships in the default application wheel rather than an optional
extra: it is a small command and uses only existing dependencies, while the
calling client alone starts it. No daemon, network listener, provider call, or
client registration is installed. The adapter supports the `2025-11-25` handshake
and a bounded read-only tool catalog; it does not claim a general MCP feature set. It accepts
standard `_meta` on supported requests and negotiates its supported version at
initialization. A tool execution/input error uses `isError: true` and the existing
CLI error JSON as text. Successful `structuredContent` follows the tool's
installed output schema; errors omit `structuredContent` so they do not falsely
claim to be a successful result schema. Text omits the CLI stream's final newline to keep
raw control characters out, while its JSON bytes otherwise match the CLI.

The next two catalog entries correspond to the existing `inspect-result` CLI
profiles for `evidence_result.v1` and `evidence_result.v2`. Each takes one local
file, emits only the CLI's `common_result_inspection.v3` structural summary, and
returns the existing typed `UNSUPPORTED_RESULT_SCHEMA` error for a mismatched
profile. The adapter does not verify the producer's claims or make a new verdict.
The Goppi baseline entry projects the existing `audit-baseline --exchange` CLI
profile from one local exchange directory to `evidence_result.v2`. It does not
invoke a Goppi verifier or infer statistical improvement from the source claims.
The two `inspect-result` action-view entries project the CLI's installed
`common_result_inspection_actions.v3` contract for v1 and v2 input files.
They count declared item actions and semantic bindings without executing actions.

The existing `audit-trajectory` CLI does not enter this default read-only catalog.
It creates a private temporary snapshot and executes a selected Goppi verifier.
The process timeout and output cap do not constrain that executable's filesystem
or network effects. A later effectful tool requires a separate decision and an
explicit startup opt-in; advertising it with read-only annotations would misstate
the current boundary.

The retained `records read-trajectory --view summary` profile is a separate
read-only tool: it checks stored bytes and direct links without invoking Goppi
or writing a record. It returns a bounded, content-minimized
`trajectory_record_read_summary.v1` result validated by the CLI. The default
full `trajectory_record_read.v2` result remains available through CLI only:
its large success payload would be duplicated in MCP text and structured content.
The compact summary can advertise its installed output schema in `tools/list`.

The `review-pair` MCP entry likewise uses an additive CLI `--view summary` profile.
The full `paired_review.v1` includes bounded but untrusted declaration strings;
the new `paired_review_summary.v1` returns only digests, closed states, counts,
and explicit interpretation limits under 16 KiB. CLI `--view full` remains the
default with its prior JSON bytes, and the MCP tool does not execute native
verification or grant approval.

The strict `review-artifacts` MCP entry uses a separate CLI `--view summary`
profile. Its output keeps source hashes, reader profiles and version state,
populations, correspondence state, finding counts, grouped verification tasks,
and limits. Pair details, row coordinates and source strings are omitted under
16 KiB. A separate `underwrite_review_artifacts_readiness` tool projects
`--view readiness-summary` with blocker code counts and explicit unknown roles,
without row coordinates. The existing `--view review` and `--view readiness`
CLI contracts keep their prior behavior.

The `records list` MCP entry uses an additive CLI `--view page` profile. It
returns up to 50 identifier-only entries, omits free-text labels, and carries
the next offset beside the observed catalog digest. A changed digest returns no
entries and requires a restart at offset zero. This detects changes between
calls; it does not create a filesystem snapshot or verify stored file bytes.

The `records show` MCP entry uses an additive CLI `--view summary` profile.
It pages at most 16 exact logical file names and closed manifest meanings,
omitting label, metadata, source locators and link targets. A continuation
reuses the top-level manifest digest; a changed digest returns no file page
and a restart offset. Declared file sizes and hashes are not byte checks.

The `records verify` MCP entry projects an additive CLI `--view summary`
profile after checking stored file bytes and direct linked manifests. It keeps
the verification verdict and checked populations as bounded check-state code
arrays. File and linked-record coordinates are omitted. A false verdict
remains a successful tool result, with no source provenance or approval claim.

The `records read-json` MCP entry uses an additive CLI `--view bounded` profile
and the existing `record_document.v1` contract. It returns all selected content
or a typed `OUTPUT_LIMIT_EXCEEDED` error, never a truncated document. The
escaped CLI JSON line including newline is limited to 16 KiB; the default full
CLI view is unchanged. A shared formatter escapes raw Unicode Cc, Cf, Zl and
Zp characters in this bounded view without changing parsed data. The selected
file is checked against its manifest; nested content is untrusted retained data,
not a tool instruction, verdict, next action, provenance or approval.

The `inspect` MCP entry uses the existing `artifact_inspection.v1` CLI result.
The result contains only bounded identifiers, digests, closed states and at
most one owner-generated diagnostic, even when the selected file is large.
CLI exit 1 for `not_created` remains a successful MCP result; invocation errors
use the existing `artifact_inspection_error.v1` text contract. It has no
release or deployment authority.

The `audit-promptfoo` MCP entry uses a new CLI `--view summary` projection of
the existing one-file actions audit. It preserves source identity, declared and
supplied populations, deltas, relation, finding code counts and fixed next
actions. Row coordinates are omitted and output is capped at 16 KiB. The
default accounting, actions and row-index CLI views remain unchanged; this
summary does not establish export completeness, producer truth or approval.

The `audit-counts` MCP entry reuses that summary contract for a manifest-bound
Promptfoo read. The inspection owner checks the contained artifact and optional
expected hash in the same read used for accounting. The summary distinguishes
`manifest_case` with `matched` or `not_supplied` from `direct_file` with
`not_applicable`; it exposes the manifest byte hash, not its paths, source
locators or authenticity. A mismatch retains the existing invocation error.

The default DeepEval accounting MCP entry uses a separate CLI `--view summary`
projection. It keeps both reader-profile count contracts, declared case-count
comparison, raw byte identity, missingness and closed next actions. Per-case
names, metric names and evaluator error text are omitted under 16 KiB. Trace
structure and owner-asserted metric bindings remain separate CLI routes; the
summary does not infer planned executions or approve a change.

The DeepEval trace MCP entry uses `--view trace-summary` over the existing
trace-index owner. It preserves supplied case/trace/span counts, source status,
parent-reference and cycle counts, limitations and next actions under 16 KiB.
Individual coordinates and parent pointers are omitted. Its linkability limit
names the remaining source hash rather than pointers absent from this view.
Parent links do not establish causality, chronology or instrumentation coverage.

The DeepEval owner-binding MCP entry uses a separate `--view bound-summary`
contract over the existing one-read TestRun plus one-read owner pack path. It
does not reuse the common-result *file* inspection contract because no result
file was read: its raw hash would have a different meaning. The bounded view
keeps source and declaration byte identities, observed occurrence population,
availability, item action and declared binding counts. Owner meaning remains
asserted and unverified; item values and coordinates are omitted under 16 KiB.

The Langfuse MCP entry uses a new CLI `--view summary` over the existing two
file accounting owner. Both blob-array and CLI-list reader contracts retain
their own counts, source hashes, scope, planned unknowns and next actions;
CLI-list retrieval/pagination metadata is also retained. Observation and score
rows are omitted under 16 KiB. The summary replaces the full view's row-pattern
linkability limit with the remaining source-hash linkability. Independent file
selection does not establish matching collection windows or completeness.

The Langfuse score-source MCP entry projects the existing CLI `--view
score-source` and installed `langfuse_score_source_summary.v1` contract. Its
known/unknown source-class counts and fixed limitations carry no score content.
The CLI now checks that this already finite result fits 16 KiB including the
newline before emission. Claimed source class does not authenticate an actor or
prove evaluator execution.

Existing CLI stdout, stderr, exit codes, and schemas are unchanged by this
decision. When the first tools ship, the README tagline ("No server") and the
`AGENT_USAGE.md` skill recipe note are updated in the same change. The ordered
program, current measurements, and success criteria are in
[the agent surface program](../plan/AGENT_SURFACE_PROGRAM.ko.md).

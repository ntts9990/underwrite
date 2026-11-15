# ADR-024: Allow a read-only local stdio MCP server over CLI result contracts

Status: accepted for planning. No MCP code is part of this decision.

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
  output, from the same owner, serialized the same way. The server does not compute
  verdicts, error meanings, limits, or next actions of its own. Where a CLI command
  lacks a suitable machine contract or a needed shape (for example a bounded result
  on overflow), that contract is extended first, with the existing limit codes such
  as `OUTPUT_LIMIT_EXCEEDED`, and the CLI uses it too.
- Measurement comes before tools: model-visible bytes of the tool list and
  descriptions get a budget and a shrink-only ratchet, and each description states
  when to use and when not to use the tool. The gate lands with the first tools.
- Untrusted strings from inputs (paths, names, free text) are not placed raw in
  model-facing text. The text content escapes control and format characters
  (Unicode Cc, Cf, Zl, Zp). Structured content must carry the same values as the
  CLI result, and a client parses it before the model sees it, so serialization
  escaping does not protect it. The result contract therefore decides how input
  strings are represented, and the CLI uses the same values; until that contract
  change lands, this risk is stated for structured content rather than claimed
  solved. Input validation failures are tool execution errors.
- Tool names use the `underwrite_` prefix so the server can sit beside other MCP
  servers in one client without collisions.

A new runtime dependency (for example the official MCP Python SDK) is not accepted
by this ADR. The implementing slice compares it with a standard-library stdio
JSON-RPC implementation on protocol conformance, maintenance cost, install
footprint, and pure-layer isolation, and records the result before adding anything.

Existing CLI stdout, stderr, exit codes, and schemas are unchanged by this
decision. When the first tools ship, the README tagline ("No server") and the
`AGENT_USAGE.md` skill recipe note are updated in the same change. The ordered
program, current measurements, and success criteria are in
[the agent surface program](../plan/AGENT_SURFACE_PROGRAM.ko.md).

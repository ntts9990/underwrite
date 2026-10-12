# ADR-019: Share the bounded descriptor reader below shell adapters

Status: Accepted

## Context

Selected record-file retrieval must hash and return the same bounded byte buffer.
The existing ingestion descriptor reader provides the required regular-file check
and budget-plus-one read. The service and ingestion layers are independent in
`.importlinter`; importing the ingestion module from the service violates that
boundary. Duplicating the loop or adding an import exemption would create two
owners or weaken the existing contract.

## Decision

Move only the descriptor reader, its chunk bound, positive-limit check, and
`TransportError` into stdlib-only `underwrite._fileio`. Place this effectful module
below the independent service/fleet/ingestion adapters and above pure modules.
Explicitly forbid pure imports of it. Keep the existing AST purity checks.

`instrument.ingest.transport` directly re-exports the existing public class and
function. Preserve object identity, diagnostic reasons, budget-plus-one behavior,
OSError propagation, and caller ownership of descriptor closure. The shared
function neither opens paths nor decides record privacy, manifest membership,
source projection, or provenance. Each adapter retains those responsibilities.

## Consequences and verification

Both consumers reuse one read implementation without a sibling-layer dependency.
This is a narrow ownership move, not a general filesystem or plugin framework.
Existing ingestion and record tests cover behavior; direct re-export identity and
import-contract checks cover the new ownership boundary. Package versions remain
unchanged.

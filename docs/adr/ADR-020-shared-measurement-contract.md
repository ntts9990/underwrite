# ADR-020: Share measurement meaning and binding contracts below pure domains

Status: Accepted

## Context

A two-sided null rejection and a calibrated forecast are different constructs from
acceptable task performance. Native measurement and acceptance need the same explicit
semantic declaration, digest and read-state invariants to prevent subject labels from
substituting for measured meaning. The domains are independent peers in `.importlinter`;
copying validation or adding an import exemption would give those meanings two owners.

## Decision

Place `underwrite.measurement_contract` below the independent pure domains and above
`underwrite_core`. This stdlib/core-only shared contract layer owns semantic value
parsing/projection/tagged identity, closed read binding validation, and deterministic
status/verdict/availability mappings. Measurement owns statistical calculations and
use-case composition. Acceptance consumes the validated binding view and its required
claim identities; it does not recalculate the statistics or import the measurement
engine. Neither domain nor the shared contract imports effectful adapters.

Keep existing legacy arithmetic and historical artifacts intact. New native policy,
read and claim versions explicitly bind measurement meaning, ordered population,
direction and declared assumptions. Historical reads remain viewable but cannot be
promoted by the current acceptance API or CLI. Package versions remain unchanged.
A semantic digest binds a supplied declaration; it does not authenticate labels,
computation, sampling, chronology or empirical construct validity.

## Consequences and verification

The import hierarchy and existing AST/pure-install checks include the new package.
No ignore-import exception, external dependency, registry or generic metric framework
is added. Cross-domain agreement is tested with construct substitution, legacy/mixed
inputs and inconsistent read states through pure APIs and installed CLI entry points.
Numerical primitives stay in their existing owners; new directional interpretation
and forecast diagnostics are tested separately from provenance and decision authority.

# ADR-021: One strict JSON decoder below independent shell adapters

Status: accepted for pin-refresh confinement.

Pin refresh must decode a caller-selected `PINS.json` before choosing any write
destination. The existing ingest transport already owns strict UTF-8, duplicate
key, nonfinite number, lone-surrogate, and depth handling. Importing ingest from
fleet would make one independent shell adapter depend on another.

Move that decoder unchanged to stdlib-only `underwrite._json`. Ingest transport
re-exports the same `DecodeError` class and `decode_json` function, preserving
existing caller identity and behavior. Fleet uses the shared decoder for its
bounded manifest read. Pure domains cannot import `_json`; the import-linter
contract and a re-export identity test guard that boundary.

This decision does not make `_json` a schema validator or storage API. Fleet
owns pin path admission, descriptor-anchored writes, and the finite refresh
refusal. The selected registry is caller authority; anchored operations do not
claim protection against hostile relocation of an already opened ancestor.

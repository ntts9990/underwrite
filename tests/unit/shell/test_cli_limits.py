"""Standalone behavior and boundary checks."""

from __future__ import annotations

from underwrite.cli import accept, gate_rules, measure

ACCEPT = {
    "CANDIDATE_MAX_BYTES": 65536,
    "CLAIMS_MAX_BYTES": 1048576,
    "DOCUMENT_MAX_DEPTH": 16,
    "READ_MAX_BYTES": 1048576,
    "READ_MAX_DEPTH": 64,
    "MAX_READS": 64,
    "OUTPUT_MAX_BYTES": 1048576,
}
MEASURE = {
    "OBSERVATION_MAX_BYTES": 4194304,
    "OBSERVATION_MAX_DEPTH": 64,
    "POLICY_MAX_BYTES": 65536,
    "POLICY_MAX_DEPTH": 16,
    "OUTPUT_MAX_BYTES": 1048576,
}
# Not one unit: RULES_MAX_DEPTH counts every YAML node, POLICY_MAX_DEPTH counts containers only.
GATE_RULES = {"RULES_MAX_BYTES": 65536, "RULES_MAX_DEPTH": 16}


def test_cli_limits_are_the_decided_literals() -> None:
    # measure first: accept derives its read bounds from measure's.
    assert {name: getattr(measure, name) for name in MEASURE} == MEASURE
    assert {name: getattr(accept, name) for name in ACCEPT} == ACCEPT
    assert {name: getattr(gate_rules, name) for name in GATE_RULES} == GATE_RULES

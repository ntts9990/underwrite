"""Card: policy-preparation | Paper: ADR-020 | Stage: agent-workflow.

Prepare consumer policies without promoting assumptions or calculating a read.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, cast

from underwrite.measurement_contract import semantics as contract

PolicyReason = Literal[
    "INVALID_POLICY_FIELDS", "LEGACY_POLICY_REQUIRES_MIGRATION", "UNSUPPORTED_POLICY_PROFILE"
]


class EventPolicyError(ValueError):
    """Finite policy diagnostics shared by preparation and measurement."""

    def __init__(self, code: str, reason: PolicyReason, location: str) -> None:
        self.code = code
        self.reason: PolicyReason = reason
        self.location = location
        super().__init__(reason)


def validate_event_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    if value.get("schema") in (
        "measurement_policy.v1",
        "measurement_policy.v2",
    ):
        raise EventPolicyError("INVALID_POLICY", "LEGACY_POLICY_REQUIRES_MIGRATION", "/policy")
    if value.get("schema") != "measurement_policy.v3" or value.get("profile") != contract.PROFILE:
        raise EventPolicyError("UNSUPPORTED_PROFILE", "UNSUPPORTED_POLICY_PROFILE", "/policy")
    try:
        p = contract.closed(
            value,
            "schema profile semantic_digest subject scope expected_case_ids "
            "eprocess assumptions diagnostics",
        )
        contract.digest(p["semantic_digest"])
        subject = contract.closed(p["subject"], "subject_id conditions")
        contract.text(subject["subject_id"])
        contract.require(subject["conditions"] == {} and p["scope"] == "aggregate")
        ids = p["expected_case_ids"]
        contract.require(isinstance(ids, list))
        ids = cast(list[object], ids)
        contract.require(0 < len(ids) <= contract.MAX_CASES)
        checked = [contract.text(item) for item in ids]
        contract.require(len(set(checked)) == len(checked))
        ep = contract.closed(p["eprocess"], "p0 alpha_total prior_a prior_b max_observations")
        contract.require(0 < contract.number(ep["p0"], 0, 1) < 1)
        contract.require(contract.number(ep["alpha_total"]) == contract.ALPHA_TOTAL)
        contract.integer(ep["prior_a"], 1, 1)
        contract.integer(ep["prior_b"], 1, 1)
        if ep["max_observations"] is not None:
            contract.integer(ep["max_observations"], 1, 2**53 - 1)
        assumptions = contract.closed(p["assumptions"], " ".join(contract.ASSUMPTIONS))
        contract.require(all(v in ("declared", "unknown") for v in assumptions.values()))
        diagnostics = contract.closed(p["diagnostics"], "ece_bins min_bin_n all_k")
        contract.integer(diagnostics["ece_bins"], 1, 128)
        contract.integer(diagnostics["min_bin_n"], 1, 2**53 - 1)
        contract.integer(diagnostics["all_k"], 1, contract.MAX_CASES)
        return p
    except contract.ContractError:
        raise EventPolicyError("INVALID_POLICY", "INVALID_POLICY_FIELDS", "/policy") from None


def prepare_event_policy(  # noqa: PLR0913 - explicit consumer policy choices
    semantics: contract.MeasurementSemantics,
    expected_case_ids: Sequence[str],
    *,
    subject_id: str,
    p0: float,
    ece_bins: int,
    min_bin_n: int,
    all_k: int,
    max_observations: int | None = None,
) -> dict[str, Any]:
    """Build a reviewable policy from declarations; every assumption stays unknown."""
    if (
        isinstance(expected_case_ids, (str, bytes))
        or not isinstance(cast(object, expected_case_ids), Sequence)
        or not 0 < len(expected_case_ids) <= contract.MAX_CASES
    ):
        raise EventPolicyError("INVALID_POLICY", "INVALID_POLICY_FIELDS", "/policy")
    policy: dict[str, Any] = {
        "schema": "measurement_policy.v3",
        "profile": contract.PROFILE,
        "semantic_digest": contract.semantic_digest(semantics),
        "subject": {"subject_id": subject_id, "conditions": {}},
        "scope": "aggregate",
        "expected_case_ids": list(expected_case_ids),
        "eprocess": {
            "p0": p0,
            "alpha_total": contract.ALPHA_TOTAL,
            "prior_a": 1,
            "prior_b": 1,
            "max_observations": max_observations,
        },
        "assumptions": dict.fromkeys(contract.ASSUMPTIONS, "unknown"),
        "diagnostics": {"ece_bins": ece_bins, "min_bin_n": min_bin_n, "all_k": all_k},
    }
    return validate_event_policy(policy)

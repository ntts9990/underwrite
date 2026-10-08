"""Standalone behavior and boundary checks."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Protocol, cast, get_args

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator

from underwrite.acceptance import claims as cl
from underwrite.acceptance import classify as cf
from underwrite.acceptance import decision as dc
from underwrite.acceptance.lifecycle import STATES
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.measurement.read import assemble_read

ROOT = repo_root(Path(__file__).resolve())
NAMES = (
    "declared_claims.v1",
    "change_candidate.v1",
    "acceptance_decision.v1",
    "acceptance_error.v1",
)
FIXTURES = ROOT / "fixtures/examples/measurement"
DIGEST = "sha256:" + "a" * 64


def schema(name: str) -> dict[str, Any]:
    return json.loads((ROOT / "contracts" / f"{name}.schema.json").read_text())


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...

    def is_valid(self, instance: object) -> bool: ...


def validator(name: str) -> Validator:
    """The packaged validator, typed; ``packaged_validator`` only loads local references."""
    return cast(Validator, packaged_validator(name))


def valid(name: str, instance: object) -> bool:
    return cast(Validator, Draft202012Validator(schema(name))).is_valid(instance)


def bound(read: dict[str, Any], subject: str) -> dict[str, Any]:
    return {**read, "subject": {"subject_id": subject, "conditions": {}}}


def claims_document() -> dict[str, Any]:
    entries = [
        {
            "claim_id": "quality",
            "subject_id": "suite.quality",
            "effect": "disqualifying",
            "policy_digest": DIGEST,
        },
        {
            "claim_id": "latency",
            "subject_id": "suite.latency",
            "effect": "validity",
            "policy_digest": DIGEST,
        },
    ]
    return {
        "schema": "declared_claims.v1",
        "candidate_id": "prompt-v2",
        "required_claims": {basis: copy.deepcopy(entries) for basis in cf.BASES},
        "selection": {"salt": "author-salt", "confirm_fraction": 0.25, "case_ids": ["c1"]},
    }


def candidate_document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "change_candidate.v1",
        "candidate_id": "prompt-v2",
        "kind": "retrieval_method",
        "basis": "held_out",
        "current_state": "screened",
        "claims_digest": cl.parse_claims(claims_document()).digest,
        "hard_failures": [],
    }
    return {**document, **changes}


def produced_reads() -> list[dict[str, Any]]:
    """Real read.v1 documents at full, partial and below-quorum availability."""
    observation = json.loads((FIXTURES / "read-input.json").read_text())
    policy = json.loads((FIXTURES / "policy.json").read_text())
    reads: list[dict[str, Any]] = []
    for quorum, extra in ((1, []), (1, ["inspect"]), (2, ["inspect"])):
        variant = {**policy, "quorum": quorum, "required_instruments": ["deepeval", *extra]}
        reads.append(
            assemble_read(observation, variant, observation_digest=DIGEST, policy_digest=DIGEST)
        )
    return reads


def decisions() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for one in produced_reads():
        for subject in ("suite.quality", "suite.latency"):
            for basis, state, hard in (
                ("held_out", "screened", []),
                ("held_in", "proposed", ["x"]),
            ):
                candidate = candidate_document(basis=basis, current_state=state, hard_failures=hard)
                found.append(
                    dc.decide(
                        candidate, claims_document(), [bound(one, subject)], {"c1": "explore"}
                    )
                )
    full = produced_reads()[0]
    passing = [bound(full, s) for s in ("suite.quality", "suite.latency")]
    for basis in ("held_out", "same_batch"):
        candidate = candidate_document(basis=basis)
        found.append(dc.decide(candidate, claims_document(), passing, {"c1": "explore"}))
    found.append(
        dc.decide(
            candidate_document(current_state="proposed"), claims_document(), [], {"c1": "explore"}
        )
    )
    return found


@pytest.mark.parametrize("name", NAMES)
def test_contract_is_a_self_contained_packaged_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema(name))
    assert packaged_validator(name).schema == schema(name)


def test_claims_contract_restates_its_owners() -> None:
    claims, read = schema("declared_claims.v1"), schema("read.v1")
    subject = read["$defs"]["subject"]["properties"]["subject_id"]
    identifier = claims["$defs"]["id"]
    assert (identifier["pattern"], identifier["minLength"]) == (
        subject["pattern"],
        subject["minLength"],
    )
    assert identifier["pattern"] == cl.ID_PATTERN
    assert claims["$defs"]["basis_claims"]["maxItems"] == cl.MAX_CLAIMS_PER_BASIS
    case_ids = claims["properties"]["selection"]["properties"]["case_ids"]
    assert case_ids["maxItems"] == cl.MAX_SELECTION_CASES
    assert tuple(claims["properties"]["required_claims"]["required"]) == cf.BASES
    assert tuple(claims["$defs"]["claim"]["properties"]["effect"]["enum"]) == cf.EFFECTS
    assert claims["$defs"]["sha256_digest"]["pattern"] == cl.DIGEST_PATTERN


def test_candidate_contract_restates_its_owners() -> None:
    candidate = schema("change_candidate.v1")["properties"]
    assert tuple(candidate["kind"]["enum"]) == dc.KINDS
    assert tuple(candidate["basis"]["enum"]) == cf.BASES
    assert tuple(candidate["current_state"]["enum"]) == STATES
    assert candidate["candidate_id"]["pattern"] == cl.ID_PATTERN
    assert candidate["claims_digest"]["pattern"] == cl.DIGEST_PATTERN
    assert candidate["hard_failures"]["maxItems"] == dc.MAX_HARD_FAILURES
    for entry in ("a", "a" * 64, "a" * 65, "x9_y", "_x", "A", "x-y", "9a", ""):
        pure = re.fullmatch(dc.HARD_FAILURE_PATTERN, entry) is not None
        assert valid("change_candidate.v1", candidate_document(hard_failures=[entry])) is pure, (
            entry
        )


def test_decision_contract_restates_its_owners() -> None:
    decision = schema("acceptance_decision.v1")
    properties, defs = decision["properties"], decision["$defs"]
    assert tuple(properties["classification"]["enum"]) == cf.CLASSIFICATIONS
    assert tuple(properties["reason_codes"]["items"]["enum"]) == cf.REASON_CODES
    assert tuple(properties["basis"]["enum"]) == cf.BASES
    assert tuple(defs["state"]["enum"]) == STATES
    assert (
        properties["claims"]["maxItems"]
        == properties["inputs"]["properties"]["read_digests"]["maxItems"]
        == cl.MAX_CLAIMS_PER_BASIS
    )
    limitations = properties["limitations"]
    assert limitations["items"]["enum"] == [*dc.LIMITATIONS, cf.PARTIAL_FAIL]
    assert [rule["contains"]["const"] for rule in limitations["allOf"]] == list(dc.LIMITATIONS)
    selection = properties["selection"]["properties"]
    declared = schema("declared_claims.v1")["properties"]["selection"]["properties"]
    assert selection["confirm_fraction"] == declared["confirm_fraction"]
    assert selection["case_count"]["maximum"] == declared["case_ids"]["maxItems"]
    assert "selection" in decision["required"]
    read_reasons = schema("read.v1")["$defs"]["reason_codes"]["items"]["enum"]
    assert defs["read_reason_codes"]["items"]["enum"] == read_reasons
    assert defs["id"]["pattern"] == cl.ID_PATTERN
    assert (
        properties["human_review"],
        properties["merge_authorized"],
        properties["basis_verified"],
    ) == (
        {"const": "required"},
        {"const": False},
        {"const": False},
    )


def test_every_emitted_decision_validates() -> None:
    check = validator("acceptance_decision.v1")
    found = decisions()
    assert {d["classification"] for d in found} == set(cf.CLASSIFICATIONS)
    for decision in found:
        check.validate(decision)


@pytest.mark.parametrize(
    "path,value",
    [
        ("merge_authorized", True),
        ("basis_verified", True),
        ("human_review", "done"),
        ("limitations", []),
        ("reason_codes", []),
        ("classification", "pass"),
        ("selection", {"confirm_fraction": 0.2, "case_count": 3, "confirm_count": 0}),
        ("selection", {"confirm_fraction": 0.2}),
        ("selection", {"case_count": 3}),
        ("selection", {"confirm_fraction": 0, "case_count": 3}),
    ],
)
def test_decision_contract_refuses_authority_and_dropped_limitations(
    path: str, value: object
) -> None:
    decision = decisions()[0]
    assert valid("acceptance_decision.v1", decision)
    assert not valid("acceptance_decision.v1", {**decision, path: value})


def test_decision_contract_requires_the_selection_summary() -> None:
    decision = decisions()[0]
    del decision["selection"]
    assert not valid("acceptance_decision.v1", decision)


def test_not_measured_summary_needs_a_reason() -> None:
    decision = next(
        d
        for d in decisions()
        if any(c["read"] and c["read"]["verdict"] == "not_measured" for c in d["claims"])
    )
    broken = copy.deepcopy(decision)
    for claim in broken["claims"]:
        if claim["read"] and claim["read"]["verdict"] == "not_measured":
            claim["read"]["reason_codes"] = []
    assert valid("acceptance_decision.v1", decision) and not valid("acceptance_decision.v1", broken)


def test_documents_the_pure_layer_accepts_validate() -> None:
    assert valid("declared_claims.v1", claims_document())
    assert valid("change_candidate.v1", candidate_document())
    over = claims_document()
    over["required_claims"]["held_in"] = [
        {"claim_id": f"c{i}", "subject_id": f"s{i}", "effect": "validity", "policy_digest": DIGEST}
        for i in range(cl.MAX_CLAIMS_PER_BASIS + 1)
    ]
    assert not valid("declared_claims.v1", over)
    with pytest.raises(cl.ClaimsInputError):
        cl.parse_claims(over)


def _refusals() -> list[dc.AcceptanceRefusal]:
    produced = produced_reads()[0]
    conditioned = {**produced, "subject": {"subject_id": "suite.quality", "conditions": {"k": "v"}}}
    scenarios: list[tuple[object, object, list[Any], dict[str, str]]] = [
        ({"human_review": "required"}, claims_document(), [], {"c1": "explore"}),
        ({}, claims_document(), [], {"c1": "explore"}),
        (candidate_document(hard_failures=["X"]), claims_document(), [], {"c1": "explore"}),
        (candidate_document(), [], [], {"c1": "explore"}),
        (candidate_document(), claims_document(), [], {"c1": "confirm"}),
        (candidate_document(candidate_id="other"), claims_document(), [], {"c1": "explore"}),
        (candidate_document(claims_digest=DIGEST), claims_document(), [], {"c1": "explore"}),
        (candidate_document(), claims_document(), [{}], {"c1": "explore"}),
        (candidate_document(), claims_document(), [produced], {"c1": "explore"}),
        (candidate_document(), claims_document(), [conditioned], {"c1": "explore"}),
        (candidate_document(current_state="rejected"), claims_document(), [], {"c1": "explore"}),
    ]
    found: list[dc.AcceptanceRefusal] = []
    for candidate, claims, reads, assignment in scenarios:
        with pytest.raises(dc.AcceptanceRefusal) as caught:
            dc.decide(candidate, claims, reads, assignment)
        found.append(caught.value)
    return found


def test_every_pure_refusal_fits_the_error_envelope() -> None:
    check = validator("acceptance_error.v1")
    codes: set[str] = set()
    for error in _refusals():
        envelope = {
            "schema": "acceptance_error.v1",
            "code": error.code,
            "reason": error.reason,
            "location": error.location,
            "next_action": error.next_action,
            "retryable": False,
            "exit_code": 2,
            **error.fields,
        }
        check.validate(envelope)
        codes.add(error.code)
    assert "CLAIMS_DIGEST_MISMATCH" in codes and "INVALID_STATE_FOR_EVALUATION" in codes


def test_error_contract_covers_every_pure_code_and_action() -> None:
    error = schema("acceptance_error.v1")["properties"]
    assert set(get_args(dc.RefusalCode)) <= set(error["code"]["enum"])
    assert set(dc.REFUSAL_ACTIONS.values()) <= set(error["next_action"]["enum"])
    context = error["context"]["properties"]
    assert tuple(context["current_state"]["enum"]) == STATES
    assert tuple(context["attempted_classification"]["enum"]) == cf.CLASSIFICATIONS
    # classify_evidence's reasons: every code but the one decide adds after projection.
    attempted = tuple(code for code in cf.REASON_CODES if code != "lifecycle_ineligible")
    assert tuple(context["reason_codes"]["items"]["enum"]) == attempted
    pattern = re.compile(error["location"]["pattern"])
    for location in (
        "",
        "/candidate",
        "/reads/12/subject/subject_id",
        "/claims/required_claims/held_out/0",
    ):
        assert pattern.match(location), location
    for location in ("/reads/0/subject/a b", "/other", "/claims/é", "/reads/0\n"):
        assert not pattern.match(location), location


def test_error_envelope_shares_the_common_error_keys_and_exits_two() -> None:
    others = [
        schema(path.name.removesuffix(".schema.json"))
        for path in sorted((ROOT / "contracts").glob("*_error.v1.schema.json"))
        if path.name != "acceptance_error.v1.schema.json"
    ]
    assert others
    required: list[set[str]] = [set(other["required"]) for other in others]
    common: set[str] = required[0].intersection(*required[1:])
    accept = schema("acceptance_error.v1")
    assert common <= set(accept["required"])
    assert accept["properties"]["exit_code"] == {"const": 2}
    for key in common - {"schema"}:
        assert all(key in other["properties"] for other in others)
    assert accept["properties"]["retryable"] == {"const": False}

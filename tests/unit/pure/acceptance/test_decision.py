"""Standalone behavior and boundary checks."""

from __future__ import annotations

import ast
import copy
import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from _repo_paths import repo_root
from underwrite_core.canonical import content_digest

from underwrite.acceptance import decision as dc
from underwrite.acceptance.claims import parse_claims
from underwrite.acceptance.decision import AcceptanceRefusal, authority_keys, decide
from underwrite.instrument.evidence.split import assign_split
from underwrite.measurement import availability
from underwrite.measurement.read import assemble_read

ROOT = repo_root(Path(__file__))
POLICY = "sha256:" + "a" * 64
OTHER_POLICY = "sha256:" + "b" * 64
ASSIGNMENT = {"c1": "explore", "c2": "explore"}
CLAIMS = (("quality", "suite.quality", "disqualifying"), ("latency", "suite.latency", "validity"))
# (verdict, state) -> (available, missing, quorum, reasons, escalate) over [deepeval, inspect].
SHAPES: dict[tuple[str, str], tuple[list[str], list[str], int, list[str], bool]] = {
    ("pass", "full"): (["deepeval", "inspect"], [], 1, [], False),
    ("warn", "full"): (["deepeval", "inspect"], [], 1, [], True),
    ("fail", "full"): (["deepeval", "inspect"], [], 1, [], False),
    ("not_measured", "full"): (["deepeval", "inspect"], [], 1, ["signal_not_measured"], True),
    ("warn", "partial"): (["deepeval"], ["inspect"], 1, ["required_instrument_missing"], True),
    ("fail", "partial"): (["deepeval"], ["inspect"], 1, ["required_instrument_error"], True),
    ("not_measured", "partial"): (
        ["deepeval"],
        ["inspect"],
        1,
        ["signal_not_measured", "required_instrument_missing"],
        True,
    ),
    ("not_measured", "below_quorum"): (
        ["deepeval"],
        ["inspect"],
        2,
        ["below_quorum", "required_instrument_missing"],
        True,
    ),
}


def claims_document() -> dict[str, Any]:
    entries = [
        {"claim_id": c, "subject_id": s, "effect": e, "policy_digest": POLICY} for c, s, e in CLAIMS
    ]
    return {
        "schema": "declared_claims.v1",
        "candidate_id": "prompt-v2",
        "required_claims": {basis: copy.deepcopy(entries) for basis in dc.BASES},
        "selection": {"salt": "author-salt", "confirm_fraction": 0.25, "case_ids": ["c1", "c2"]},
    }


def candidate_document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "change_candidate.v1",
        "candidate_id": "prompt-v2",
        "kind": "prompt_version",
        "basis": "same_batch",
        "current_state": "proposed",
        "claims_digest": parse_claims(claims_document()).digest,
        "hard_failures": [],
    }
    return {**document, **changes}


def read(subject: str, verdict: str = "pass", state: str = "full") -> dict[str, Any]:
    available, missing, quorum, reasons, escalate = SHAPES[(verdict, state)]
    return {
        "schema": "read.v1",
        "subject": {"subject_id": subject, "conditions": {}},
        "verdict": verdict,
        "score": None if verdict == "not_measured" else 0.5,
        "escalate": escalate,
        "reason_codes": list(reasons),
        "availability": {
            "required_instruments": ["deepeval", "inspect"],
            "quorum": quorum,
            "available": list(available),
            "missing": list(missing),
            "state": state,
            "triggered": state != "full",
        },
        "sources": {"observations": ["sha256:" + "c" * 64], "policy": POLICY},
    }


def all_pass() -> list[dict[str, Any]]:
    return [read("suite.quality"), read("suite.latency")]


def refusal(
    candidate: object = None,
    claims: object = None,
    reads: list[Any] | None = None,
    assignment: dict[str, str] | None = None,
) -> AcceptanceRefusal:
    with pytest.raises(AcceptanceRefusal) as caught:
        decide(
            candidate_document() if candidate is None else candidate,
            claims_document() if claims is None else claims,
            all_pass() if reads is None else reads,
            ASSIGNMENT if assignment is None else assignment,
        )
    return caught.value


def run(candidate: dict[str, Any], reads: list[dict[str, Any]]) -> dict[str, Any]:
    return decide(candidate, claims_document(), reads, ASSIGNMENT)


# --- the decision document ------------------------------------------------------------


def test_screened_decision_is_the_whole_document() -> None:
    candidate, reads = candidate_document(), all_pass()
    reads[1]["reason_codes"] = []
    digests = {r["subject"]["subject_id"]: content_digest(r) for r in reads}

    def summary(claim_id: str, subject: str, effect: str) -> dict[str, Any]:
        found = {"digest": digests[subject], "verdict": "pass", "escalate": False}
        return {
            "claim_id": claim_id,
            "subject_id": subject,
            "effect": effect,
            "policy_digest": POLICY,
            "read": {**found, "reason_codes": []},
        }

    assert run(candidate, reads) == {
        "schema": "acceptance_decision.v1",
        "candidate_id": "prompt-v2",
        "basis": "same_batch",
        "classification": "screened",
        "from_state": "proposed",
        "next_state": "screened",
        "reason_codes": ["stage_screened"],
        "claims": [
            summary("latency", "suite.latency", "validity"),
            summary("quality", "suite.quality", "disqualifying"),
        ],
        "inputs": {
            "claims_digest": parse_claims(claims_document()).digest,
            "candidate_digest": content_digest(candidate),
            "read_digests": sorted(digests.values()),
        },
        "selection": {"confirm_fraction": 0.25, "case_count": 2},
        "limitations": sorted(dc.LIMITATIONS),
        "human_review": "required",
        "merge_authorized": False,
        "basis_verified": False,
    }


def test_limitations_name_every_unverified_precondition() -> None:
    assert sorted(dc.LIMITATIONS) == sorted(
        [
            "basis_self_declared",
            "claims_sealing_is_digest_binding",
            "split_parameters_author_chosen",
            "reads_trusted_as_supplied",
            "read_not_bound_to_candidate",
            "hard_failures_declared",
            "escalate_not_decisive",
        ]
    )


def test_a_tiny_author_chosen_fraction_is_visible_in_the_selection_summary() -> None:
    claims = claims_document()
    cases = [f"case-{index}" for index in range(40)]
    claims["selection"] = {"salt": "author-salt", "confirm_fraction": 5e-324, "case_ids": cases}
    split = assign_split(cases, salt="author-salt", confirm_fraction=5e-324)
    assert split.confirm_count == 0  # a vacuous check: no case can leak
    assignment = {item.case_id: item.split for item in split.assignments}
    candidate = candidate_document(claims_digest=parse_claims(claims).digest)
    decision = decide(candidate, claims, all_pass(), assignment)
    assert decision["selection"] == {"confirm_fraction": 5e-324, "case_count": 40}
    assert "split_parameters_author_chosen" in decision["limitations"]


def test_missing_claim_is_a_null_read_and_indeterminate() -> None:
    decision = run(candidate_document(), [read("suite.quality")])
    assert decision["classification"] == "indeterminate"
    assert decision["next_state"] == "proposed"
    assert decision["reason_codes"] == ["missing_required_claim"]
    assert [c["read"] is None for c in decision["claims"]] == [True, False]
    assert decision["inputs"]["read_digests"] == [content_digest(read("suite.quality"))]


def test_not_measured_and_warn_summaries_carry_escalate_and_reasons() -> None:
    reads = [
        read("suite.quality", "warn", "partial"),
        read("suite.latency", "not_measured", "below_quorum"),
    ]
    decision = run(candidate_document(current_state="screened"), reads)
    assert decision["classification"] == "indeterminate"
    assert decision["next_state"] == "screened"
    assert decision["reason_codes"] == ["claim_warn", "claim_not_measured"]
    latency, quality = (c["read"] for c in decision["claims"])
    assert latency["verdict"] == "not_measured" and latency["escalate"] is True
    assert latency["reason_codes"] == ["below_quorum", "required_instrument_missing"]
    assert quality["verdict"] == "warn" and quality["reason_codes"] == [
        "required_instrument_missing"
    ]


def test_held_out_all_pass_on_a_proposed_candidate_is_lifecycle_ineligible() -> None:
    decision = run(candidate_document(basis="held_out"), all_pass())
    assert decision["classification"] == "indeterminate"
    assert (decision["from_state"], decision["next_state"]) == ("proposed", "proposed")
    assert decision["reason_codes"] == ["lifecycle_ineligible"]


def test_held_out_all_pass_on_a_screened_candidate_is_confirmed() -> None:
    decision = run(candidate_document(basis="held_out", current_state="screened"), all_pass())
    assert decision["classification"] == "confirmed"
    assert decision["next_state"] == "confirmed"
    assert decision["reason_codes"] == ["stage_confirmed"]
    assert decision["basis_verified"] is False and decision["merge_authorized"] is False


def test_declared_hard_failure_classifies_the_terminal_negative_state() -> None:
    candidate = candidate_document(hard_failures=["write_outside_harness"])
    decision = run(candidate, all_pass())
    assert (decision["classification"], decision["next_state"]) == ("rejected", "rejected")
    assert decision["reason_codes"] == ["hard_failure"]


@pytest.mark.parametrize(
    "subject,expected",
    [
        ("suite.quality", ("rejected", "disqualifying_claim_failed")),
        ("suite.latency", ("indeterminate", "validity_claim_failed")),
    ],
)
def test_partial_fail_counts_as_measured_fail_and_says_so(
    subject: str, expected: tuple[str, str]
) -> None:
    reads = [
        read(subject, "fail", "partial"),
        *(r for r in all_pass() if r["subject"]["subject_id"] != subject),
    ]
    decision = run(candidate_document(), reads)
    assert (decision["classification"], decision["reason_codes"][0]) == expected
    assert decision["limitations"] == sorted(
        [*dc.LIMITATIONS, "measured_fail_under_partial_availability"]
    )
    full = run(candidate_document(), [read(subject, "fail"), *reads[1:]])
    assert full["limitations"] == sorted(dc.LIMITATIONS)


@pytest.mark.parametrize("current", ["confirmed", "rejected"])
@pytest.mark.parametrize("hard", [False, True])
def test_terminal_state_is_refused_with_the_attempted_classification(
    current: str, hard: bool
) -> None:
    candidate = candidate_document(
        basis="held_out",
        current_state=current,
        hard_failures=["write_outside_harness"] if hard else [],
    )
    error = refusal(candidate)
    assert (error.code, error.reason, error.location) == (
        "INVALID_STATE_FOR_EVALUATION",
        "INVALID_STATE_FOR_EVALUATION",
        "/candidate/current_state",
    )
    assert error.next_action == "CORRECT_CANDIDATE"
    attempted = ("rejected", ["hard_failure"]) if hard else ("confirmed", ["stage_confirmed"])
    assert error.fields == {
        "context": {
            "current_state": current,
            "attempted_classification": attempted[0],
            "reason_codes": attempted[1],
        }
    }


def test_inputs_are_not_mutated() -> None:
    candidate, claims, reads = candidate_document(), claims_document(), all_pass()
    before = copy.deepcopy((candidate, claims, reads))
    decide(candidate, claims, reads, dict(ASSIGNMENT))
    assert (candidate, claims, reads) == before


# --- authority fields (D26) -----------------------------------------------------------


def test_authority_keys_finds_keys_at_any_depth_and_ignores_values() -> None:
    document = {
        "a": [{"b": {"review_action": 1}}, [[{"approve": None}]]],
        "note": "merge_authorized",
        "human_review": "required",
    }
    assert authority_keys(document) == ("approve", "human_review", "review_action")
    assert authority_keys(["approval", {"x": ["basis_verified"]}]) == ()
    assert authority_keys({"x": {"basis_verified": False, "merge_authorized": False}}) == (
        "basis_verified",
        "merge_authorized",
    )
    assert authority_keys("approval") == ()
    deep: Any = {"approval": True}
    for _ in range(5000):
        deep = [deep]
    assert authority_keys(deep) == ("approval",)
    assert dc.AUTHORITY_KEYS == {
        "merge_authorized",
        "basis_verified",
        "human_review",
        "approve",
        "approval",
        "review_action",
    }


@pytest.mark.parametrize("key", sorted(dc.AUTHORITY_KEYS))
def test_authority_field_refused_before_any_shape_check(key: str) -> None:
    candidate: dict[str, Any] = {"schema": "not a candidate", "x": [{key: False}]}
    error = refusal(candidate)
    assert (error.code, error.location, error.next_action) == (
        "AUTHORITY_FIELD_REFUSED",
        "/candidate",
        "CORRECT_CANDIDATE",
    )
    claims = claims_document()
    claims["required_claims"]["held_in"][0][key] = "x"
    error = refusal(claims=claims)
    assert (error.code, error.location, error.next_action) == (
        "AUTHORITY_FIELD_REFUSED",
        "/claims",
        "CORRECT_CLAIMS",
    )


def test_authority_fields_in_a_read_are_not_the_scan_s_business() -> None:
    foreign = {"human_review": True, "merge_authorized": False}
    assert authority_keys(foreign) == ("human_review", "merge_authorized")
    error = refusal(reads=[foreign])
    assert (error.code, error.location) == ("INVALID_READ", "/reads/0")


# --- candidate and claims ---------------------------------------------------------------


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "change_candidate.v2"),
        ("candidate_id", "prompt v2"),
        ("candidate_id", 7),
        ("kind", "patch"),
        ("basis", "sealed_test"),
        ("current_state", "shipped"),
        ("claims_digest", "sha256:" + "A" * 64),
        ("claims_digest", None),
    ],
)
def test_invalid_candidate_field_is_located(field: str, value: object) -> None:
    error = refusal(candidate_document(**{field: value}))
    assert (error.code, error.reason, error.location) == (
        "INVALID_CANDIDATE",
        "INVALID_CANDIDATE",
        f"/candidate/{field}",
    )


@pytest.mark.parametrize(
    "candidate",
    [
        [],
        "candidate",
        {k: v for k, v in candidate_document().items() if k != "hard_failures"},
        {**candidate_document(), "extra": 1},
    ],
)
def test_candidate_that_is_not_the_contract_shape(candidate: object) -> None:
    error = refusal(candidate)
    assert (error.code, error.location) == ("INVALID_CANDIDATE", "/candidate")


@pytest.mark.parametrize(
    "hard",
    [None, "write_outside_harness", [1], ["Write"], ["_x"], ["x-y"], ["a" * 65], ["a"] * 65],
)
def test_invalid_hard_failures(hard: object) -> None:
    error = refusal(candidate_document(hard_failures=hard))
    assert (error.code, error.reason, error.location) == (
        "INVALID_CANDIDATE",
        "INVALID_HARD_FAILURE",
        "/candidate/hard_failures",
    )


def test_hard_failure_bounds_are_inclusive() -> None:
    for hard in (["a" * 64], ["a"] * 64, ["x9_y"]):
        assert (
            run(candidate_document(hard_failures=hard), all_pass())["classification"] == "rejected"
        )


def test_invalid_claims_carry_the_claims_code_and_structural_location() -> None:
    claims = claims_document()
    del claims["required_claims"]["held_out"]
    error = refusal(claims=claims)
    assert (error.code, error.reason, error.location, error.next_action) == (
        "INVALID_CLAIMS",
        "MISSING_BASIS",
        "/claims/required_claims/held_out",
        "CORRECT_CLAIMS",
    )
    error = refusal(claims=[])
    assert (error.code, error.reason, error.location) == (
        "INVALID_CLAIMS",
        "INVALID_CLAIMS",
        "/claims",
    )
    error = refusal(assignment={"c1": "explore"})
    assert (error.code, error.reason, error.location) == (
        "INVALID_CLAIMS",
        "INVALID_SELECTION",
        "/claims/selection/case_ids",
    )


def test_selection_leak_names_no_case_id() -> None:
    error = refusal(assignment={"c1": "explore", "c2": "confirm"})
    assert (error.code, error.reason, error.location) == (
        "SELECTION_LEAKS_CONFIRM",
        "SELECTION_LEAKS_CONFIRM",
        "/claims/selection/case_ids",
    )
    assert "c2" not in str(error)


def test_candidate_mismatch_precedes_the_digest_check() -> None:
    error = refusal(
        candidate_document(candidate_id="prompt-v3", claims_digest="sha256:" + "0" * 64)
    )
    assert (error.code, error.location) == ("CANDIDATE_MISMATCH", "/candidate/candidate_id")


def test_claims_digest_mismatch_shows_the_computed_digest() -> None:
    error = refusal(candidate_document(claims_digest="sha256:" + "0" * 64))
    assert (error.code, error.location) == ("CLAIMS_DIGEST_MISMATCH", "/candidate/claims_digest")
    assert error.fields == {"claims_digest": parse_claims(claims_document()).digest}


def test_refusal_order_candidate_then_claims_then_selection_then_bindings() -> None:
    assert refusal({"schema": 1}, claims=[]).code == "INVALID_CANDIDATE"
    assert refusal(claims=[], assignment={}).code == "INVALID_CLAIMS"
    leak = {"c1": "confirm", "c2": "explore"}
    assert refusal(candidate_document(candidate_id="other"), assignment=leak).code == (
        "SELECTION_LEAKS_CONFIRM"
    )
    assert refusal(candidate_document(claims_digest="sha256:" + "0" * 64), reads=[{}]).code == (
        "CLAIMS_DIGEST_MISMATCH"
    )


# --- read bindings (D2, D11) -------------------------------------------------------------


def _observation() -> dict[str, Any]:
    return json.loads((ROOT / "fixtures/examples/measurement/read-input.json").read_text())


def _broken(path: str, value: object) -> dict[str, Any]:
    broken = read("suite.quality")
    *parents, name = path.split(".")
    holder: Any = broken
    for key in parents:
        holder = holder[key]
    if value is KeyError:
        del holder[name]
    else:
        holder[name] = value
    return broken


@pytest.mark.parametrize(
    "path,value",
    [
        ("schema", "observation.v1"),
        ("subject", "suite.quality"),
        ("subject.subject_id", 3),
        ("subject.conditions", []),
        ("verdict", "unknown"),
        ("verdict", KeyError),
        ("escalate", 1),
        ("reason_codes", "below_quorum"),
        ("reason_codes", [1]),
        ("sources", KeyError),
        ("sources.policy", None),
        ("availability", []),
        ("availability.state", "absent"),
        ("availability.quorum", True),
        ("availability.quorum", 1.0),
        ("availability.triggered", 0),
        ("availability.required_instruments", "deepeval"),
        ("availability.available", KeyError),
        ("availability.missing", [None]),
        ("score", float("nan")),
    ],
)
def test_non_read_documents_are_invalid_reads(path: str, value: object) -> None:
    error = refusal(reads=[read("suite.latency"), _broken(path, value)])
    assert (error.code, error.location, error.next_action) == (
        "INVALID_READ",
        "/reads/1",
        "CORRECT_READ",
    )


@pytest.mark.parametrize("document", ["foreign", "observation", "list", "text"])
def test_other_documents_passed_as_reads_are_refused(document: str) -> None:
    other = {
        "foreign": {"schema": "foreign.decision.v1"},
        "observation": _observation(),
        "list": [read("suite.quality")],
        "text": "read.v1",
    }[document]
    error = refusal(reads=[other])
    assert (error.code, error.location) == ("INVALID_READ", "/reads/0")


def test_real_producer_reads_bind() -> None:
    policy = json.loads((ROOT / "fixtures/examples/measurement/policy.json").read_text())
    produced: list[dict[str, Any]] = []
    for quorum, extra in ((1, []), (1, ["inspect"]), (2, ["inspect"])):
        variant = {**policy, "quorum": quorum, "required_instruments": ["deepeval", *extra]}
        one = assemble_read(
            _observation(), variant, observation_digest=POLICY, policy_digest=POLICY
        )
        produced.append(one)
    states = [one["availability"]["state"] for one in produced]
    assert states == ["full", "partial", "below_quorum"]
    for one in produced:
        one["subject"]["subject_id"] = "suite.quality"
        decision = run(candidate_document(), [one])
        assert decision["claims"][1]["read"]["verdict"] == one["verdict"]


def test_read_for_a_claim_outside_the_basis_is_unexpected() -> None:
    error = refusal(reads=[read("suite.quality"), read("suite.cost")])
    assert (error.code, error.location) == ("UNEXPECTED_CLAIM", "/reads/1/subject/subject_id")


def test_other_basis_claims_bind_no_read() -> None:
    claims = claims_document()
    claims["required_claims"]["held_out"].append(
        {
            "claim_id": "cost",
            "subject_id": "suite.cost",
            "effect": "validity",
            "policy_digest": POLICY,
        }
    )
    candidate = candidate_document(claims_digest=parse_claims(claims).digest)
    with pytest.raises(AcceptanceRefusal) as caught:
        decide(candidate, claims, [read("suite.cost")], ASSIGNMENT)
    assert caught.value.code == "UNEXPECTED_CLAIM"


def test_two_reads_for_one_claim_are_duplicate() -> None:
    error = refusal(
        reads=[read("suite.quality"), read("suite.latency"), read("suite.quality", "fail")]
    )
    assert (error.code, error.location) == ("DUPLICATE_CLAIM", "/reads/2/subject/subject_id")


def test_read_under_another_policy_is_refused() -> None:
    other = read("suite.latency")
    other["sources"]["policy"] = OTHER_POLICY
    error = refusal(reads=[read("suite.quality"), other])
    assert (error.code, error.location) == ("READ_POLICY_MISMATCH", "/reads/1/sources/policy")


def test_conditioned_read_is_not_aggregate() -> None:
    conditioned = read("suite.latency")
    conditioned["subject"]["conditions"] = {"locale": "ko"}
    error = refusal(reads=[conditioned])
    assert (error.code, error.location) == ("READ_NOT_AGGREGATE", "/reads/0/subject/conditions")


def test_binding_refusals_follow_subject_policy_aggregate_availability() -> None:
    bad = read("suite.cost")
    bad["sources"]["policy"] = OTHER_POLICY
    bad["subject"]["conditions"] = {"k": "v"}
    bad["availability"]["state"] = "partial"
    assert refusal(reads=[bad]).code == "UNEXPECTED_CLAIM"
    bad["subject"]["subject_id"] = "suite.quality"
    assert refusal(reads=[bad]).code == "READ_POLICY_MISMATCH"
    bad["sources"]["policy"] = POLICY
    assert refusal(reads=[bad]).code == "READ_NOT_AGGREGATE"
    bad["subject"]["conditions"] = {}
    assert refusal(reads=[bad]).code == "READ_AVAILABILITY_INCONSISTENT"


BELOW = ["below_quorum", "required_instrument_missing"]
BOTH = ["deepeval", "inspect"]
# name -> (top-level changes, availability changes) applied to a consistent partial warn.
INCONSISTENCIES: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {
    # schema-caught shapes (read.v1 measured_read / availability)
    "pass_partial": ({"verdict": "pass"}, {}),
    "measured_below_quorum": ({"reason_codes": BELOW}, {"quorum": 2, "state": "below_quorum"}),
    "full_with_missing": ({}, {"state": "full"}),
    "triggered_off": ({}, {"triggered": False}),
    "partial_not_escalated": ({"escalate": False}, {}),
    # schema-valid inconsistencies
    "full_with_loss_reason": (
        {"verdict": "pass", "escalate": False, "reason_codes": ["required_instrument_error"]},
        {"available": BOTH, "missing": [], "state": "full", "triggered": False},
    ),
    "partial_below_quorum_count": ({}, {"quorum": 2}),
    "below_quorum_reason_at_partial": ({"reason_codes": BELOW}, {}),
    "partial_without_loss_reason": ({"reason_codes": []}, {}),
    "below_quorum_without_reason": (
        {"verdict": "not_measured", "reason_codes": ["required_instrument_missing"]},
        {"quorum": 2, "state": "below_quorum"},
    ),
    "instrument_outside_required": ({}, {"missing": ["judge"]}),
    "instrument_in_both": ({}, {"available": BOTH}),
    "duplicate_required": ({}, {"required_instruments": ["deepeval", "deepeval", "inspect"]}),
    "duplicate_available": (
        {},
        {
            "available": ["deepeval", "deepeval"],
            "required_instruments": ["deepeval", "deepeval", "inspect"],
        },
    ),
}


def _inconsistent(change: str) -> dict[str, Any]:
    top, panel = INCONSISTENCIES[change]
    changed = read("suite.quality", "warn", "partial")
    changed.update(copy.deepcopy(top))
    changed["availability"].update(copy.deepcopy(panel))
    return changed


@pytest.mark.parametrize("change", INCONSISTENCIES)
def test_read_whose_verdict_contradicts_its_availability_is_refused(change: str) -> None:
    assert dc.consistent_availability(read("suite.quality", "warn", "partial"))
    error = refusal(reads=[read("suite.latency"), _inconsistent(change)])
    assert (error.code, error.location) == (
        "READ_AVAILABILITY_INCONSISTENT",
        "/reads/1/availability",
    )


def test_every_admitted_shape_is_consistent() -> None:
    for verdict, state in SHAPES:
        assert dc.consistent_availability(read("s", verdict, state)), (verdict, state)
    assert set(SHAPES) == set(dc.CONSISTENT_READS)


def test_loss_reasons_are_availability_s() -> None:
    assert set(availability.LOSS_REASON.values()) == dc.LOSS_REASONS


# (verdict, required, available, quorum, reasons): panels no quorum policy admits
# (make_policy refuses each); read.v1 refuses only the zero quorum.
UNREACHABLE: dict[str, tuple[str, list[str], list[str], int, list[str]]] = {
    "no_instruments_full_pass": ("pass", [], [], 1, []),
    "quorum_zero_partial_fail": ("fail", ["deepeval"], [], 0, ["required_instrument_missing"]),
    "quorum_above_panel": ("not_measured", ["deepeval"], [], 2, BELOW),
}


@pytest.mark.parametrize("name", UNREACHABLE)
def test_panel_outside_every_policy_is_inconsistent(name: str) -> None:
    verdict, required, reported, quorum, reasons = UNREACHABLE[name]
    with pytest.raises(availability.AvailabilityInputError):
        availability.make_policy(required, quorum)
    missing = [one for one in required if one not in reported]
    state = "full" if not missing else "partial" if len(reported) >= quorum else "below_quorum"
    panel = {
        "required_instruments": required,
        "quorum": quorum,
        "available": reported,
        "missing": missing,
        "state": state,
        "triggered": state != "full",
    }
    one = read("suite.quality", verdict)
    one.update(reason_codes=reasons, escalate=state != "full", availability=panel)
    assert not dc.consistent_availability(one)
    error = refusal(reads=[one])
    assert (error.code, error.location) == (
        "READ_AVAILABILITY_INCONSISTENT",
        "/reads/0/availability",
    )


def _assessed(required: tuple[str, ...], quorum: int, reported: tuple[str, ...], loss: str) -> Any:
    policy = availability.make_policy(required, quorum)
    losses: dict[object, object] = {name: loss for name in required if name not in reported}
    return availability.assess(policy, reported, losses)


GRID = [
    (required, quorum, reported, loss)
    for size in (1, 2, 3)
    for required in [("deepeval", "inspect", "judge")[:size]]
    for quorum in range(1, size + 1)
    for count in range(size + 1)
    for reported in itertools.combinations(required, count)
    for loss in ("missing", "error")
]


@pytest.mark.parametrize("required,quorum,reported,loss", GRID)
def test_state_from_counts_is_availability_assess(
    required: tuple[str, ...], quorum: int, reported: tuple[str, ...], loss: str
) -> None:
    """Every read the cap and the panel-less branch can produce is consistent; any other
    state for the same panel is not."""
    assessed = _assessed(required, quorum, reported, loss)
    panel = availability.project(assessed)
    produced: list[dict[str, Any]] = []
    for verdict in ("pass", "warn", "fail"):
        capped = availability.cap(availability.make_panel_read(verdict, 0.5, False), assessed)
        produced.append({**availability.project_capped(capped), "availability": panel})
    unmeasured = ["signal_not_measured"]
    if assessed.state == "below_quorum":
        unmeasured.append("below_quorum")
    unmeasured.extend(availability.loss_reasons(assessed))
    produced.append(
        {
            "verdict": "not_measured",
            "escalate": True,
            "reason_codes": unmeasured,
            "availability": panel,
        }
    )
    for one in produced:
        assert dc.consistent_availability(one), one
        for other in ("full", "partial", "below_quorum"):
            if other != assessed.state:
                moved = {**one, "availability": {**panel, "state": other}}
                assert not dc.consistent_availability(moved), (one, other)


def test_grid_reaches_every_state() -> None:
    assert {_assessed(*case).state for case in GRID} == {"full", "partial", "below_quorum"}


# --- structure ---------------------------------------------------------------------------


def test_decision_module_does_not_reference_the_fold() -> None:
    tree = ast.parse(Path(dc.__file__).read_text())
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    names |= {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert not names & {"FOLD", "fold_term", "reverse_term"}


def test_refusal_actions_follow_the_location_root() -> None:
    assert dc.REFUSAL_ACTIONS == {
        "candidate": "CORRECT_CANDIDATE",
        "claims": "CORRECT_CLAIMS",
        "reads": "CORRECT_READ",
    }
    error = AcceptanceRefusal("INVALID_READ", "/reads/3/subject")
    assert (error.reason, error.fields, error.next_action) == ("INVALID_READ", {}, "CORRECT_READ")
    assert str(error) == "INVALID_READ: INVALID_READ at /reads/3/subject"

"""The run audit conserves declared states without inventing comparisons."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from underwrite.instrument.evidence.run_audit import RunAuditError, audit_run_inventory

EXPECTED_INITIAL_UNIT_COUNT = 5
EXPECTED_CANDIDATE_PLAN = 3
EXPECTED_EXTENDED_REPORTED = 5
EXPECTED_EXTENDED_ONES = 2
EXPECTED_UNIQUE_CASES = 2
EXPECTED_TWO_GROUP_UNITS = 6
CONTRACTS = Path(__file__).resolve().parents[4] / "contracts"


def inventory() -> dict[str, Any]:
    records = [
        {
            "unit_id": "u-one",
            "case_id": "same-case",
            "arm": "baseline",
            "repeat_index": 0,
            "outcome": 1,
            "source_reason": None,
            "source_refs": [{"kind": "row", "ref": "rows#1", "digest": None}],
        },
        {
            "unit_id": "u-zero",
            "case_id": "same-case",
            "arm": "baseline",
            "repeat_index": 1,
            "outcome": 0,
            "source_reason": "ERROR text remains an observed zero",
            "source_refs": [{"kind": "row", "ref": "rows#2", "digest": None}],
        },
        {
            "unit_id": "u-missing",
            "case_id": "other-case",
            "arm": "candidate",
            "repeat_index": 0,
            "missing_reason": "verifier",
            "source_reason": "verifier unavailable",
            "source_refs": [{"kind": "row", "ref": "rows#3", "digest": None}],
        },
        {
            "unit_id": "u-endpoint",
            "case_id": "other-case",
            "arm": "candidate",
            "repeat_index": 1,
            "non_binary_classification": "endpoint_failure",
            "source_reason": "no binary result",
            "source_refs": [{"kind": "row", "ref": "rows#4", "digest": None}],
        },
    ]
    return {
        "schema": "binary_run_inventory.v1",
        "source": {
            "producer": "fixture",
            "format": "fixture.run",
            "format_version": "1",
            "artifact_digest": "sha256:" + "a" * 64,
            "files": [{"name": "rows", "sha256": "sha256:" + "b" * 64, "bytes": 12}],
        },
        "outcome_definition": {"name": "success", "positive_means": "task passed"},
        "unit": "producer_execution_unit",
        "groups": [
            {
                "group_id": "g-one",
                "context": {"revision": "one", "synthetic": True, "note": None},
                "planned_units": [
                    {"unit_id": record["unit_id"], "arm": record["arm"]} for record in records
                ]
                + [{"unit_id": "u-unreported", "arm": "candidate"}],
                "records": records,
            }
        ],
    }


def report(value: object) -> dict[str, Any]:
    return cast(dict[str, Any], audit_run_inventory(value))


def test_all_states_are_conserved_and_non_binary_is_not_missing_or_zero() -> None:
    result = report(inventory())
    group = result["groups"][0]
    accounting = group["accounting"]
    assert result["group_count"] == 1
    assert result["unit_count"] == EXPECTED_INITIAL_UNIT_COUNT
    assert accounting == {
        "planned": 5,
        "reported": 4,
        "observed_one": 1,
        "observed_zero": 1,
        "missing_by_reason": {"infra": 0, "verifier": 1, "not_started": 0, "unknown": 0},
        "non_binary_by_classification": [{"classification": "endpoint_failure", "count": 1}],
        "unreported_planned": 1,
        "unique_cases": 2,
    }
    assert (
        sum(
            [
                accounting["observed_one"],
                accounting["observed_zero"],
                *accounting["missing_by_reason"].values(),
                *(item["count"] for item in accounting["non_binary_by_classification"]),
            ]
        )
        == accounting["reported"]
    )
    assert "NON_BINARY_CLASSIFICATIONS_EXCLUDED_FROM_BINARY_OUTCOMES" in result["limitations"]
    assert "effect" not in result and "rate" not in result and "verdict" not in result


def test_plan_only_arm_is_retained_and_unknown_plan_stays_unknown() -> None:
    value = inventory()
    value["groups"][0]["records"] = value["groups"][0]["records"][:2]
    known = report(value)["groups"][0]
    candidate = next(item for item in known["arms"] if item["arm"] == "candidate")
    assert candidate["planned"] == EXPECTED_CANDIDATE_PLAN
    assert candidate["reported"] == 0
    assert candidate["unreported_planned"] == EXPECTED_CANDIDATE_PLAN
    value["groups"][0]["planned_units"] = None
    unknown = report(value)["groups"][0]
    assert unknown["accounting"]["planned"] is None
    assert unknown["accounting"]["unreported_planned"] is None


def test_same_case_arm_and_repeat_do_not_collapse_distinct_producer_units() -> None:
    value = inventory()
    copy = deepcopy(value["groups"][0]["records"][0])
    copy["unit_id"] = "u-another"
    value["groups"][0]["records"].append(copy)
    value["groups"][0]["planned_units"].append({"unit_id": "u-another", "arm": "baseline"})
    accounting = report(value)["groups"][0]["accounting"]
    assert accounting["reported"] == EXPECTED_EXTENDED_REPORTED
    assert accounting["observed_one"] == EXPECTED_EXTENDED_ONES
    assert accounting["unique_cases"] == EXPECTED_UNIQUE_CASES


@pytest.mark.parametrize("mutation", ["duplicate", "outside", "wrong_arm"])
def test_known_plan_enforces_membership_arm_and_unique_units(mutation: str) -> None:
    value = inventory()
    record = value["groups"][0]["records"][0]
    if mutation == "duplicate":
        value["groups"][0]["records"].append(deepcopy(record))
        code = "DUPLICATE_UNIT_ID"
    elif mutation == "outside":
        record["unit_id"] = "outside"
        code = "UNPLANNED_UNIT"
    else:
        record["arm"] = "candidate"
        code = "PLANNED_ARM_MISMATCH"
    with pytest.raises(RunAuditError) as exc:
        report(value)
    assert exc.value.code == code


def test_groups_are_separate_and_sorted_without_pooled_outcomes() -> None:
    value = inventory()
    second = deepcopy(value["groups"][0])
    second["group_id"] = "a-group"
    second["planned_units"] = None
    second["records"] = [second["records"][0]]
    second["records"][0]["unit_id"] = "group-two-unit"
    value["groups"].append(second)
    result = report(value)
    assert [group["group_id"] for group in result["groups"]] == ["a-group", "g-one"]
    assert result["unit_count"] == EXPECTED_TWO_GROUP_UNITS
    assert "accounting" not in result


def test_reordering_set_like_source_facts_does_not_change_digest() -> None:
    value = inventory()
    value["source"]["files"].append({"name": "first", "sha256": "sha256:" + "c" * 64, "bytes": 1})
    reordered = deepcopy(value)
    reordered["source"]["files"].reverse()
    reordered["groups"][0]["records"].reverse()
    reordered["groups"][0]["planned_units"].reverse()
    assert report(value)["input_digest"] == report(reordered)["input_digest"]


@pytest.mark.parametrize(
    "bad",
    [
        "e\u0301",
        "x" * 129,
        " ",
    ],
)
def test_bounded_nfc_identifiers_are_checked_before_canonicalization(bad: str) -> None:
    value = inventory()
    value["groups"][0]["records"][0]["unit_id"] = bad
    with pytest.raises(RunAuditError) as exc:
        report(value)
    assert exc.value.code == "INVALID_INPUT"


def test_record_state_is_exactly_one_of_three() -> None:
    value = inventory()
    value["groups"][0]["records"][0]["missing_reason"] = "infra"
    with pytest.raises(RunAuditError) as exc:
        report(value)
    assert exc.value.code == "INVALID_INPUT"


def test_source_reason_is_bounded_without_reclassifying_the_record() -> None:
    value = inventory()
    value["groups"][0]["records"][0]["source_reason"] = "r" * 256
    assert report(value)["groups"][0]["accounting"]["observed_one"] == 1
    value["groups"][0]["records"][0]["source_reason"] += "x"
    with pytest.raises(RunAuditError) as exc:
        report(value)
    assert exc.value.code == "INVALID_INPUT"


def test_empty_known_plan_differs_from_unknown_plan() -> None:
    value = inventory()
    value["groups"][0]["records"] = []
    value["groups"][0]["planned_units"] = []
    known = report(value)["groups"][0]["accounting"]
    assert known["planned"] == 0 and known["unreported_planned"] == 0
    value["groups"][0]["planned_units"] = None
    unknown = report(value)["groups"][0]["accounting"]
    assert unknown["planned"] is None and unknown["unreported_planned"] is None


def test_shared_input_and_output_definitions_do_not_drift() -> None:
    inventory_contract = json.loads(
        (CONTRACTS / "binary_run_inventory.v1.schema.json").read_text(encoding="utf-8")
    )
    audit_contract = json.loads(
        (CONTRACTS / "binary_run_audit.v1.schema.json").read_text(encoding="utf-8")
    )
    shared = {
        "text128",
        "text512",
        "digest",
        "source",
        "outcome_definition",
        "source_ref",
        "record",
    }
    assert {name: inventory_contract["$defs"][name] for name in shared} == {
        name: audit_contract["$defs"][name] for name in shared
    }

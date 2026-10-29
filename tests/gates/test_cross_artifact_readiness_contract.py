"""Generated readiness schema stays closed and packaged with its review source."""

from __future__ import annotations

import copy
import importlib.util
import json
import tomllib
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError

from underwrite.cli.local_json import Validator
from underwrite.instrument.ingest.schema import packaged_validator

ROOT = Path(__file__).resolve().parents[2]
NAME = "cross_artifact_readiness.v1"
FILE = f"{NAME}.schema.json"
DIGEST = "sha256:" + "a" * 64


def _blocker(code: str) -> dict[str, object]:
    suffix = {
        "PROMPTFOO_CASE_METADATA_REQUIRED": "/testCase/metadata",
        "PROMPTFOO_CASE_ID_REQUIRED": "/testCase/metadata/case_id",
        "PROMPTFOO_SLOT_KIND_REQUIRED": "/testCase/metadata/slot_kind",
    }[code]
    return {
        "row_index": 0,
        "code": code,
        "location": "/promptfoo/results/results/0" + suffix,
    }


def _schema() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads((ROOT / "contracts" / FILE).read_bytes()))


def _report(*, complete: bool) -> dict[str, Any]:
    return {
        "schema": NAME,
        "relation": "incomparable_on_supplied_evidence",
        "source_identity": {
            "goppi_assessment_sha256": DIGEST,
            "goppi_result_sha256": DIGEST,
            "promptfoo_export_sha256": DIGEST,
        },
        "readers": {
            "goppi": {
                "profile": "goppi.c55-selected-fields.v1",
                "assessment_schema": "goppi.operator_answer_exchange_assessment.v1",
                "result_schema": "evidence_result.v1",
            },
            "promptfoo": {
                "profile": "promptfoo.summary-v3.selected-fields.v1",
                "producer_version": None,
                "producer_version_state": "unknown",
            },
        },
        "populations": {
            "goppi": {
                "unit": "case",
                "planned_count": 1,
                "item_count": 1,
                "availability": {"observed": 1, "missing": 0, "error": 0, "abstained": 0},
            },
            "promptfoo": {
                "unit": "row",
                "supplied_rows": 1,
                "subject_rows": 1 if complete else None,
                "control_rows": 0 if complete else None,
                "planned_count": None,
                "category_counts": {"successes": 1, "failures": 0, "errors": 0},
            },
        },
        "metadata": {
            "case_ids": "complete" if complete else "incomplete",
            "row_roles": "source_declared" if complete else "incomplete",
            "blockers": []
            if complete
            else [
                _blocker("PROMPTFOO_CASE_ID_REQUIRED"),
                _blocker("PROMPTFOO_SLOT_KIND_REQUIRED"),
            ],
        },
        "review_prerequisites": (
            "needs_owner_correspondence" if complete else "missing_owner_metadata"
        ),
        "finding_counts": {"equal": 0, "different": 0, "ineligible": 0},
        "improvement": "unknown",
        "checks": {
            "declaration_source_hashes": "not_supplied",
            "goppi_assessment_result_binding": "checked_selected_fields",
            "goppi_terminal_controls": "not_reperformed",
            "goppi_portable_closure": "not_reperformed",
            "source_authentication": "not_independently_verified",
            "promptfoo_accounting": "consistent",
        },
        "next_verification_tasks": [
            {
                "code": (
                    "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"
                    if complete
                    else "PROVIDE_PROMPTFOO_CASE_METADATA"
                ),
                "owner": "evaluation_owner",
                "case_index": None,
                "row_index": None if complete else 0,
            }
        ],
        "limitations": ["SOURCE_CLAIMS_UNVERIFIED", "NO_IMPROVEMENT_OR_APPROVAL"],
    }


def test_generated_authored_and_packaged_bytes_agree() -> None:
    name = "build_cross_artifact_readiness"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generated = (json.dumps(module.build(), indent=2, ensure_ascii=False) + "\n").encode()
    owner = ROOT / "contracts" / FILE
    packaged = ROOT / "src/underwrite/_contracts" / FILE
    assert owner.read_bytes() == generated
    assert packaged.is_symlink()
    assert packaged.readlink() == Path("../../../contracts") / FILE
    assert packaged.read_bytes() == generated
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert f"contracts/{FILE}" in config["tool"]["uv"]["build-backend"]["source-include"]
    Draft202012Validator.check_schema(_schema())


@pytest.mark.parametrize("complete", [False, True])
def test_both_readiness_states_validate(complete: bool) -> None:
    cast(Validator, packaged_validator(NAME)).validate(_report(complete=complete))


@pytest.mark.parametrize("missing_field", ["case_ids", "row_roles"])
def test_either_missing_metadata_dimension_keeps_owner_prerequisite(missing_field: str) -> None:
    report = _report(complete=True)
    report["metadata"][missing_field] = "incomplete"
    code = (
        "PROMPTFOO_CASE_ID_REQUIRED"
        if missing_field == "case_ids"
        else "PROMPTFOO_SLOT_KIND_REQUIRED"
    )
    report["metadata"]["blockers"] = [_blocker(code)]
    report["review_prerequisites"] = "missing_owner_metadata"
    report["next_verification_tasks"][0]["code"] = "PROVIDE_PROMPTFOO_CASE_METADATA"
    if missing_field == "row_roles":
        report["populations"]["promptfoo"]["subject_rows"] = None
        report["populations"]["promptfoo"]["control_rows"] = None
    cast(Validator, packaged_validator(NAME)).validate(report)


def test_complete_metadata_allows_zero_subject_rows_with_specific_next_task() -> None:
    report = _report(complete=True)
    report["populations"]["promptfoo"].update(subject_rows=0, control_rows=1)
    report["review_prerequisites"] = "missing_subject_rows"
    report["next_verification_tasks"][0]["code"] = "PROVIDE_PROMPTFOO_SUBJECT_ROWS"
    cast(Validator, packaged_validator(NAME)).validate(report)


def test_disjoint_case_ids_have_a_distinct_owner_prerequisite() -> None:
    report = _report(complete=True)
    report["review_prerequisites"] = "missing_matching_case_ids"
    report["next_verification_tasks"][0]["code"] = "PROVIDE_MATCHING_SOURCE_CASE_IDS"
    cast(Validator, packaged_validator(NAME)).validate(report)


def test_missing_metadata_object_blocks_both_dimensions() -> None:
    report = _report(complete=False)
    report["metadata"]["blockers"] = [_blocker("PROMPTFOO_CASE_METADATA_REQUIRED")]
    cast(Validator, packaged_validator(NAME)).validate(report)


@pytest.mark.parametrize(
    ("prerequisite", "subject_rows", "task_code"),
    [
        ("missing_subject_rows", 1, "PROVIDE_PROMPTFOO_SUBJECT_ROWS"),
        ("missing_matching_case_ids", 0, "PROVIDE_MATCHING_SOURCE_CASE_IDS"),
        ("needs_owner_correspondence", 0, "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"),
    ],
)
def test_complete_metadata_prerequisite_requires_matching_subject_population(
    prerequisite: str, subject_rows: int, task_code: str
) -> None:
    report = _report(complete=True)
    report["review_prerequisites"] = prerequisite
    report["populations"]["promptfoo"].update(
        subject_rows=subject_rows, control_rows=1 - subject_rows
    )
    report["next_verification_tasks"][0]["code"] = task_code
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)


@pytest.mark.parametrize(
    ("prerequisite", "wrong_code"),
    [
        ("missing_owner_metadata", "PROVIDE_PROMPTFOO_SUBJECT_ROWS"),
        ("missing_subject_rows", "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"),
        ("missing_matching_case_ids", "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"),
        ("needs_owner_correspondence", "PROVIDE_PROMPTFOO_CASE_METADATA"),
    ],
)
def test_prerequisite_requires_corresponding_first_owner_task(
    prerequisite: str, wrong_code: str
) -> None:
    report = _report(complete=prerequisite != "missing_owner_metadata")
    if prerequisite == "missing_subject_rows":
        report["populations"]["promptfoo"].update(subject_rows=0, control_rows=1)
    report["review_prerequisites"] = prerequisite
    report["next_verification_tasks"][0]["code"] = wrong_code
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)


def test_prerequisite_first_task_requires_evaluation_owner() -> None:
    report = _report(complete=True)
    report["next_verification_tasks"][0]["owner"] = "calling_agent"
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)


@pytest.mark.parametrize(
    ("case_ids", "row_roles", "blockers"),
    [
        ("complete", "incomplete", ["PROMPTFOO_CASE_ID_REQUIRED", "PROMPTFOO_SLOT_KIND_REQUIRED"]),
        (
            "incomplete",
            "source_declared",
            ["PROMPTFOO_CASE_ID_REQUIRED", "PROMPTFOO_SLOT_KIND_REQUIRED"],
        ),
        ("complete", "incomplete", ["PROMPTFOO_CASE_METADATA_REQUIRED"]),
        ("incomplete", "source_declared", ["PROMPTFOO_CASE_METADATA_REQUIRED"]),
        ("incomplete", "incomplete", ["PROMPTFOO_CASE_ID_REQUIRED"]),
        ("incomplete", "incomplete", ["PROMPTFOO_SLOT_KIND_REQUIRED"]),
    ],
)
def test_metadata_statuses_require_matching_blocker_codes(
    case_ids: str, row_roles: str, blockers: list[str]
) -> None:
    report = _report(complete=False)
    report["metadata"].update(
        case_ids=case_ids,
        row_roles=row_roles,
        blockers=[_blocker(code) for code in blockers],
    )
    if row_roles == "source_declared":
        report["populations"]["promptfoo"].update(subject_rows=1, control_rows=0)
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema",), "cross_artifact_review.v1"),
        (("relation",), "declared_case_mapping_descriptive"),
        (("finding_counts", "equal"), 1),
        (("source_identity", "promptfoo_export_path"), "/private/result.json"),
        (("metadata", "blockers", 0, "case_id"), "private-case"),
        (("next_verification_tasks", 0, "command"), "cat /private/result.json"),
        (("populations", "promptfoo", "supplied_rows"), 1001),
        (("metadata", "blockers", 0, "location"), "/private/result.json"),
        (("metadata", "blockers"), []),
        (("review_prerequisites",), "needs_owner_correspondence"),
        (("review_prerequisites",), "missing_subject_rows"),
        (("populations", "promptfoo", "subject_rows"), 1),
        (("checks", "promptfoo_accounting"), "missing"),
    ],
)
def test_refuses_impossible_states_and_secret_fields(
    path: tuple[str | int, ...], value: object
) -> None:
    report = copy.deepcopy(_report(complete=False))
    target: Any = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)


@pytest.mark.parametrize("mutation", ["null_count", "blocker", "wrong_prerequisite"])
def test_complete_metadata_has_no_missing_metadata_state(mutation: str) -> None:
    report = copy.deepcopy(_report(complete=True))
    if mutation == "null_count":
        report["populations"]["promptfoo"]["control_rows"] = None
    elif mutation == "blocker":
        report["metadata"]["blockers"] = _report(complete=False)["metadata"]["blockers"]
    else:
        report["review_prerequisites"] = "missing_owner_metadata"
    with pytest.raises(ValidationError):
        cast(Validator, packaged_validator(NAME)).validate(report)

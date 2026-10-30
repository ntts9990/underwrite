"""A readiness view names missing owner metadata without inventing case comparisons."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator, ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.cli.main import main
from underwrite.instrument.ingest.cross_artifact import (
    MAX_PROMPTFOO_ROWS,
    CrossArtifactResultError,
    review_artifact_readiness_files,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

ROOT = Path(__file__).resolve().parents[3]
C55 = ROOT / "fixtures/conformance/goppi-c55-common"
PROMPTFOO = ROOT / "fixtures/golden/external/promptfoo-0.124.1/results.json"
ERROR_EXIT = 2
ROW_COUNT = 7
SUBJECT_COUNT = ROW_COUNT - 1


def _arguments(promptfoo: Path = PROMPTFOO) -> list[str]:
    return [
        "review-artifacts",
        "--goppi-assessment",
        str(C55 / "assessment.json"),
        "--goppi-result",
        str(C55 / "evidence-result.json"),
        "--promptfoo",
        str(promptfoo),
    ]


def _readiness(promptfoo: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main([*_arguments(promptfoo), "--view", "readiness", "--json"]) == 0
    streams = capsys.readouterr()
    assert streams.err == ""
    return cast(dict[str, Any], json.loads(streams.out))


def _source(path: Path, *, canary: str = "") -> dict[str, Any]:
    source = cast(dict[str, Any], json.loads(PROMPTFOO.read_bytes()))
    if canary:
        source["results"]["results"][0]["prompt"] = canary
    path.write_text(json.dumps(source), encoding="utf-8")
    return source


@pytest.mark.parametrize(
    "failure", ["rejected_report", "dangling_reference", "schema_configuration"]
)
def test_generated_readiness_validation_failure_is_not_input_failure(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    from underwrite.instrument.ingest import cross_artifact

    original = cross_artifact.packaged_validator
    broken = Draft202012Validator(
        {"not": {}} if failure == "rejected_report" else {"$ref": "#/$defs/missing"}
    )

    def validator(name: str) -> Draft202012Validator:
        if name == cross_artifact.READINESS_SCHEMA:
            if failure == "schema_configuration":
                raise SchemaConfigurationError("WRONG_INSPECTION_CONTRACT")
            return broken
        return original(name)

    monkeypatch.setattr(cross_artifact, "packaged_validator", validator)
    assert main([*_arguments(), "--view", "readiness", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    assert error["schema"] == "evidence_error.v1"
    assert (error["code"], error["reason"], error["next_action"]) == (
        ("INTERNAL_ERROR", "CROSS_ARTIFACT_RESULT_INVALID", "REPORT_INTERNAL_ERROR")
        if failure == "rejected_report"
        else ("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", "REINSTALL_PACKAGE")
    )
    if failure != "rejected_report":
        with pytest.raises(SchemaConfigurationError):
            review_artifact_readiness_files(
                C55 / "assessment.json", C55 / "evidence-result.json", PROMPTFOO
            )
    else:
        with pytest.raises(CrossArtifactResultError) as raised:
            review_artifact_readiness_files(
                C55 / "assessment.json", C55 / "evidence-result.json", PROMPTFOO
            )
        assert raised.value.code == "INTERNAL_ERROR"


def test_native_export_returns_bound_readiness_without_invented_roles(
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = _readiness(PROMPTFOO, capsys)
    assert report == review_artifact_readiness_files(
        C55 / "assessment.json", C55 / "evidence-result.json", PROMPTFOO
    )
    assert report["schema"] == "cross_artifact_readiness.v1"
    assert report["relation"] == "incomparable_on_supplied_evidence"
    assert report["source_identity"]["promptfoo_export_sha256"] == digest_bytes(
        PROMPTFOO.read_bytes()
    )
    assert report["checks"]["goppi_assessment_result_binding"] == "checked_selected_fields"
    assert report["checks"]["promptfoo_accounting"] == "consistent"
    assert report["metadata"]["case_ids"] == "incomplete"
    assert report["metadata"]["row_roles"] == "incomplete"
    assert report["review_prerequisites"] == "missing_owner_metadata"
    assert {item["code"] for item in report["metadata"]["blockers"]} == {
        "PROMPTFOO_CASE_ID_REQUIRED",
        "PROMPTFOO_SLOT_KIND_REQUIRED",
    }
    assert report["populations"]["promptfoo"] == {
        "unit": "row",
        "supplied_rows": 7,
        "subject_rows": None,
        "control_rows": None,
        "planned_count": None,
        "category_counts": {"successes": 3, "failures": 1, "errors": 3},
    }
    assert report["finding_counts"] == {"equal": 0, "different": 0, "ineligible": 0}
    assert report["improvement"] == "unknown"
    assert report["next_verification_tasks"][0]["code"] == "PROVIDE_PROMPTFOO_CASE_METADATA"
    assert "pairs" not in report

    assert main([*_arguments(), "--json"]) == ERROR_EXIT
    refused = capsys.readouterr()
    assert refused.out == ""
    assert json.loads(refused.err)["reason"] == "PROMPTFOO_CASE_ID_REQUIRED"

    assert main([*_arguments(), "--view", "readiness"]) == 0
    human = capsys.readouterr()
    assert "missing_owner_metadata" in human.out
    assert "No case comparison" in human.out
    assert str(PROMPTFOO) not in human.out + human.err


def test_declared_roles_without_case_ids_are_counted_but_not_joined(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    source = _source(path)
    for row in source["results"]["results"]:
        row["testCase"]["metadata"]["slot_kind"] = "subject"
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert report["metadata"]["case_ids"] == "incomplete"
    assert report["metadata"]["row_roles"] == "source_declared"
    assert report["populations"]["promptfoo"]["subject_rows"] == ROW_COUNT
    assert report["populations"]["promptfoo"]["control_rows"] == 0
    assert report["review_prerequisites"] == "missing_owner_metadata"
    assert all(
        item["code"] == "PROMPTFOO_CASE_ID_REQUIRED"
        for item in report["metadata"]["blockers"]
    )


def test_complete_source_metadata_only_enables_a_future_owner_declaration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    source = _source(path)
    case_ids = [
        case["case_id"]
        for case in json.loads((C55 / "assessment.json").read_bytes())["cases"]
    ]
    for index, row in enumerate(source["results"]["results"]):
        metadata = row["testCase"]["metadata"]
        metadata["case_id"] = f"control-{index}" if index == 0 else case_ids[index - 1]
        metadata["slot_kind"] = "control" if index == 0 else "subject"
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert report["metadata"] == {
        "case_ids": "complete",
        "row_roles": "source_declared",
        "blockers": [],
    }
    assert report["review_prerequisites"] == "needs_owner_correspondence"
    assert report["populations"]["promptfoo"]["subject_rows"] == SUBJECT_COUNT
    assert report["populations"]["promptfoo"]["control_rows"] == 1
    assert (
        report["populations"]["promptfoo"]["subject_rows"]
        + report["populations"]["promptfoo"]["control_rows"]
        == report["populations"]["promptfoo"]["supplied_rows"]
    )
    assert (
        report["next_verification_tasks"][0]["code"]
        == "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"
    )
    assert report["finding_counts"] == {"equal": 0, "different": 0, "ineligible": 0}

    assert main([*_arguments(path), "--json"]) == 0
    strict = json.loads(capsys.readouterr().out)
    assert strict["schema"] == "cross_artifact_review.v1"
    assert strict["relation"] == "incomparable_on_supplied_evidence"


def test_all_control_rows_request_subject_rows_before_case_mapping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "all-controls.json"
    source = _source(path)
    for index, row in enumerate(source["results"]["results"]):
        row["testCase"]["metadata"].update(
            {"case_id": f"control-{index}", "slot_kind": "control"}
        )
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    promptfoo = report["populations"]["promptfoo"]
    assert report["metadata"]["blockers"] == []
    assert promptfoo["subject_rows"] == 0
    assert promptfoo["control_rows"] == ROW_COUNT
    assert report["review_prerequisites"] == "missing_subject_rows"
    assert report["next_verification_tasks"][0]["code"] == "PROVIDE_PROMPTFOO_SUBJECT_ROWS"
    assert report["finding_counts"] == {"equal": 0, "different": 0, "ineligible": 0}

    assert main([*_arguments(path), "--json"]) == 0
    strict = json.loads(capsys.readouterr().out)
    assert strict["relation"] == "incomparable_on_supplied_evidence"
    assert strict["populations"]["promptfoo"]["subject_rows"] == 0
    assert strict["next_verification_tasks"][0]["code"] == "PROVIDE_PROMPTFOO_SUBJECT_ROWS"
    invalid = deepcopy(strict)
    invalid["next_verification_tasks"][0]["code"] = "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"
    with pytest.raises(ValidationError):
        packaged_validator("cross_artifact_review.v1").validate(invalid)  # pyright: ignore[reportUnknownMemberType]


def test_disjoint_source_case_ids_need_new_export_before_mapping(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "disjoint.json"
    source = _source(path)
    case_ids = {
        case["case_id"]
        for case in json.loads((C55 / "assessment.json").read_bytes())["cases"]
    }
    for index, row in enumerate(source["results"]["results"]):
        row["testCase"]["metadata"].update(
            {"case_id": f"underwrite-no-match-{index}", "slot_kind": "subject"}
        )
    assert case_ids.isdisjoint(
        row["testCase"]["metadata"]["case_id"] for row in source["results"]["results"]
    )
    path.write_text(json.dumps(source), encoding="utf-8")

    readiness = _readiness(path, capsys)
    assert readiness["metadata"]["blockers"] == []
    assert readiness["populations"]["promptfoo"]["subject_rows"] == ROW_COUNT
    assert readiness["review_prerequisites"] == "missing_matching_case_ids"
    assert readiness["next_verification_tasks"][0]["code"] == "PROVIDE_MATCHING_SOURCE_CASE_IDS"

    assert main([*_arguments(path), "--json"]) == 0
    streams = capsys.readouterr()
    strict = json.loads(streams.out)
    assert streams.err == ""
    assert strict["relation"] == "incomparable_on_supplied_evidence"
    assert strict["next_verification_tasks"][0]["code"] == "PROVIDE_MATCHING_SOURCE_CASE_IDS"
    assert "underwrite-no-match" not in streams.out


def test_missing_ids_and_all_control_roles_report_both_owner_prerequisites(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "controls-without-ids.json"
    source = _source(path)
    for row in source["results"]["results"]:
        row["testCase"]["metadata"]["slot_kind"] = "control"
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert report["metadata"]["case_ids"] == "incomplete"
    assert report["metadata"]["row_roles"] == "source_declared"
    assert report["populations"]["promptfoo"]["subject_rows"] == 0
    assert report["review_prerequisites"] == "missing_owner_metadata"
    assert [task["code"] for task in report["next_verification_tasks"][:2]] == [
        "PROVIDE_PROMPTFOO_CASE_METADATA",
        "PROVIDE_PROMPTFOO_SUBJECT_ROWS",
    ]


def test_empty_export_needs_subject_rows_without_inventing_a_case_map(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "empty.json"
    path.write_text(
        json.dumps(
            {
                "results": {
                    "version": 3,
                    "stats": {"successes": 0, "failures": 0, "errors": 0},
                    "results": [],
                }
            }
        ),
        encoding="utf-8",
    )
    report = _readiness(path, capsys)
    assert report["metadata"]["blockers"] == []
    assert report["populations"]["promptfoo"]["supplied_rows"] == 0
    assert report["review_prerequisites"] == "missing_subject_rows"
    assert report["next_verification_tasks"][0]["code"] == "PROVIDE_PROMPTFOO_SUBJECT_ROWS"

    assert main([*_arguments(path), "--json"]) == 0
    strict = json.loads(capsys.readouterr().out)
    assert strict["populations"]["promptfoo"]["subject_rows"] == 0
    assert strict["next_verification_tasks"][0]["code"] == "PROVIDE_PROMPTFOO_SUBJECT_ROWS"


def test_mixed_metadata_is_incomplete_and_source_content_stays_private(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    canary = "PRIVATE_PROMPTFOO_REVIEW_CANARY"
    path = tmp_path / (canary + ".json")
    source = _source(path, canary=canary)
    rows = source["results"]["results"]
    rows[0]["testCase"]["metadata"].update({"case_id": "one", "slot_kind": "subject"})
    rows[1]["testCase"]["metadata"]["slot_kind"] = "control"
    rows[2]["testCase"]["metadata"]["case_id"] = "two"
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert report["metadata"]["case_ids"] == "incomplete"
    assert report["metadata"]["row_roles"] == "incomplete"
    assert report["populations"]["promptfoo"]["subject_rows"] is None
    assert report["populations"]["promptfoo"]["control_rows"] is None
    assert {item["row_index"] for item in report["metadata"]["blockers"]} == set(range(1, 7))
    assert canary not in json.dumps(report)


def test_missing_test_case_is_a_structural_blocker_not_an_empty_subject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    source = _source(path)
    del source["results"]["results"][0]["testCase"]
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert report["metadata"]["blockers"][0] == {
        "row_index": 0,
        "code": "PROMPTFOO_CASE_METADATA_REQUIRED",
        "location": "/promptfoo/results/results/0/testCase",
    }
    assert report["populations"]["promptfoo"]["subject_rows"] is None


def test_readiness_captures_each_selected_file_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwrite.instrument.ingest import cross_artifact as adapter

    original = adapter.read_local_file
    reads: list[Path] = []

    def read(path: Path, limit: int) -> bytes:
        reads.append(path)
        return original(path, limit)

    monkeypatch.setattr(adapter, "read_local_file", read)
    adapter.review_artifact_readiness_files(
        C55 / "assessment.json", C55 / "evidence-result.json", PROMPTFOO
    )
    assert reads == [C55 / "assessment.json", C55 / "evidence-result.json", PROMPTFOO]


def test_readiness_keeps_invalid_sources_and_declarations_as_typed_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "export.json"
    source = _source(path)
    source["results"]["stats"]["failures"] += 1
    path.write_text(json.dumps(source), encoding="utf-8")
    assert main([*_arguments(path), "--view", "readiness", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["reason"] == "PROMPTFOO_ACCOUNTING_INCONSISTENT"

    source["results"]["stats"]["failures"] -= 1
    source["results"]["version"] = 4
    path.write_text(json.dumps(source), encoding="utf-8")
    assert main([*_arguments(path), "--view", "readiness", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["reason"] == "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED"

    assert (
        main([*_arguments(), "--view", "readiness", "--correspondence", str(path), "--json"])
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["code"] == "USAGE_ERROR"


def test_goppi_selected_binding_drift_stays_a_refusal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment = tmp_path / "assessment.json"
    result = tmp_path / "result.json"
    assessment.write_bytes((C55 / "assessment.json").read_bytes() + b" ")
    result.write_bytes((C55 / "evidence-result.json").read_bytes())
    assert (
        main(
            [
                "review-artifacts",
                "--goppi-assessment",
                str(assessment),
                "--goppi-result",
                str(result),
                "--promptfoo",
                str(PROMPTFOO),
                "--view",
                "readiness",
                "--json",
            ]
        )
        == ERROR_EXIT
    )
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["reason"] == "C55_RESULT_SOURCE_MISMATCH"


def test_readiness_blockers_remain_bounded_at_the_supported_row_limit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "many.json"
    source: dict[str, Any] = {
        "results": {
            "version": 3,
            "stats": {"successes": MAX_PROMPTFOO_ROWS, "failures": 0, "errors": 0},
            "results": [
                {"success": True, "failureReason": 0, "testCase": {"metadata": {}}}
                for _ in range(MAX_PROMPTFOO_ROWS)
            ],
        }
    }
    path.write_text(json.dumps(source), encoding="utf-8")
    report = _readiness(path, capsys)
    assert len(report["metadata"]["blockers"]) == 2 * MAX_PROMPTFOO_ROWS
    assert report["populations"]["promptfoo"]["subject_rows"] is None

    source["results"]["stats"]["successes"] += 1
    source["results"]["results"].append(
        {"success": True, "failureReason": 0, "testCase": {"metadata": {}}}
    )
    path.write_text(json.dumps(source), encoding="utf-8")
    assert main([*_arguments(path), "--view", "readiness", "--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err)["reason"] == "ROW_LIMIT_EXCEEDED"


def test_readiness_view_is_advertised_by_capabilities() -> None:
    from underwrite.cli import capabilities
    from underwrite.cli import main as cli_main

    commands = cast(
        list[dict[str, Any]], capabilities.report(cli_main.COMMAND_DESCRIPTORS)["commands"]
    )
    command = next(item for item in commands if item["command"] == "review-artifacts")
    profiles = cast(list[dict[str, Any]], command["profiles"])
    readiness = next(
        item for item in profiles if item["name"] == "goppi-c55|promptfoo-summary-v3|readiness"
    )
    assert readiness["required_inputs"] == [
        "goppi_assessment",
        "goppi_result",
        "promptfoo",
        "view",
    ]
    assert readiness["output_schemas"] == ["cross_artifact_readiness.v1"]

"""The public cross-artifact entry point preserves source claims and refusals."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from underwrite_core.canonical import canonical_bytes, digest_bytes

from underwrite.cli.main import main
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.cross_artifact import (
    CrossArtifactResultError,
    review_artifact_files,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

ROOT = Path(__file__).resolve().parents[3]
C55 = ROOT / "fixtures/conformance/goppi-c55-common"
CANARY = "PRIVATE_CASE_AND_ANSWER_CANARY"
ERROR_EXIT = 2


def _write(path: Path, document: object) -> Path:
    path.write_bytes(canonical_bytes(document))
    return path


def _promptfoo(case_ids: list[str]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [
        {
            "success": False,
            "failureReason": 1,
            "testCase": {"metadata": {"case_id": case_ids[0], "slot_kind": "control"}},
            "prompt": {"raw": CANARY},
        }
    ]
    # The second subject intentionally differs from the C55 false field. Missing,
    # abstained and error C55 cases remain ineligible despite Promptfoo false.
    for index, case_id in enumerate(case_ids):
        success = index in (0, 1)
        rows.append(
            {
                "success": success,
                "failureReason": 0 if success else 1,
                "testCase": {"metadata": {"case_id": case_id, "slot_kind": "subject"}},
                "response": {"output": CANARY},
            }
        )
    return {
        "results": {
            "version": 3,
            "results": rows,
            "stats": {"successes": 2, "failures": 6, "errors": 0},
        },
        "metadata": {"promptfooVersion": "0.123.0"},
    }


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any]]:
    assessment = tmp_path / "assessment.json"
    result = tmp_path / "result.json"
    assessment.write_bytes((C55 / "assessment.json").read_bytes())
    result.write_bytes((C55 / "evidence-result.json").read_bytes())
    case_ids = [case["case_id"] for case in json.loads(assessment.read_bytes())["cases"]]
    promptfoo = _promptfoo(case_ids)
    promptfoo_path = _write(tmp_path / "promptfoo.json", promptfoo)
    return assessment, result, promptfoo_path, promptfoo, json.loads(result.read_bytes())


def _declaration(
    tmp_path: Path,
    paths: tuple[Path, Path, Path],
    *,
    coverage: str = "complete",
    pairs: list[dict[str, object]] | None = None,
) -> tuple[Path, dict[str, Any]]:
    assessment, result, promptfoo = paths
    case_ids = [case["case_id"] for case in json.loads(assessment.read_bytes())["cases"]]
    document: dict[str, Any] = {
        "schema": "cross_artifact_correspondence.v1",
        "scope": "owner_declared_case_mapping",
        "declared_by": CANARY,
        "coverage": coverage,
        "sources": {
            "goppi_assessment_sha256": digest_bytes(assessment.read_bytes()),
            "goppi_result_sha256": digest_bytes(result.read_bytes()),
            "promptfoo_export_sha256": digest_bytes(promptfoo.read_bytes()),
        },
        "pairs": pairs
        if pairs is not None
        else [
            {"case_index": index, "row_index": index + 1, "case_id": case_id}
            for index, case_id in enumerate(case_ids)
        ],
    }
    return _write(tmp_path / "correspondence.json", document), document


def _invoke(
    capsys: pytest.CaptureFixture[str], paths: tuple[Path, Path, Path], declaration: Path | None
) -> tuple[int, dict[str, Any], str, str]:
    assessment, result, promptfoo = paths
    args = [
        "review-artifacts",
        "--goppi-assessment",
        str(assessment),
        "--goppi-result",
        str(result),
        "--promptfoo",
        str(promptfoo),
    ]
    if declaration is not None:
        args.extend(("--correspondence", str(declaration)))
    code = main([*args, "--json"])
    streams = capsys.readouterr()
    return code, json.loads(streams.out or streams.err), streams.out, streams.err


@pytest.mark.parametrize(
    "failure", ["rejected_report", "dangling_reference", "schema_configuration"]
)
def test_generated_review_validation_failure_is_not_input_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    from underwrite.instrument.ingest import cross_artifact

    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    original = cross_artifact.packaged_validator
    broken = Draft202012Validator(
        {"not": {}} if failure == "rejected_report" else {"$ref": "#/$defs/missing"}
    )

    def validator(name: str) -> Draft202012Validator:
        if name == cross_artifact.RESULT_SCHEMA:
            if failure == "schema_configuration":
                raise SchemaConfigurationError("WRONG_INSPECTION_CONTRACT")
            return broken
        return original(name)

    monkeypatch.setattr(cross_artifact, "packaged_validator", validator)
    code, error, stdout, stderr = _invoke(capsys, paths, None)
    assert code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert (error["code"], error["reason"], error["next_action"]) == (
        ("INTERNAL_ERROR", "CROSS_ARTIFACT_RESULT_INVALID", "REPORT_INTERNAL_ERROR")
        if failure == "rejected_report"
        else ("SCHEMA_CONFIGURATION_ERROR", "INVALID_PACKAGED_CONTRACT", "REINSTALL_PACKAGE")
    )
    assert CANARY not in stderr
    if failure != "rejected_report":
        with pytest.raises(SchemaConfigurationError):
            cross_artifact.review_artifact_files(*paths)
    else:
        with pytest.raises(CrossArtifactResultError) as raised:
            cross_artifact.review_artifact_files(*paths)
        assert raised.value.code == "INTERNAL_ERROR"


def test_no_map_is_incomparable_and_cli_equals_python_api(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    code, report, stdout, stderr = _invoke(capsys, paths, None)

    assert code == 0 and stderr == ""
    assert stdout.encode() == canonical_bytes(report) + b"\n"
    assert report == review_artifact_files(*paths)
    packaged_validator("cross_artifact_review.v1").validate(report)  # pyright: ignore[reportUnknownMemberType]
    assert report["relation"] == "incomparable_on_supplied_evidence"
    assert report["correspondence"]["pairs"] == []
    assert report["correspondence"]["unmatched_case_indices"] == list(range(7))
    assert report["correspondence"]["unmatched_subject_row_indices"] == list(range(1, 8))
    assert report["populations"]["promptfoo"] == {
        "unit": "row",
        "supplied_rows": 8,
        "subject_rows": 7,
        "control_rows": 1,
        "planned_count": None,
        "category_counts": {"successes": 2, "failures": 6, "errors": 0},
    }
    assert report["finding_counts"] == {"equal": 0, "different": 0, "ineligible": 0}
    assert "PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE" in {
        task["code"] for task in report["next_verification_tasks"]
    }
    assert report["improvement"] == "unknown"
    assert CANARY not in stdout


def test_complete_map_reports_equal_difference_and_all_ineligible_states(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    declaration, _ = _declaration(tmp_path, paths)
    code, report, stdout, stderr = _invoke(capsys, paths, declaration)

    assert code == 0 and stderr == ""
    assert report == review_artifact_files(*paths, declaration)
    assert report["relation"] == "declared_case_mapping_descriptive"
    assert report["finding_counts"] == {"equal": 1, "different": 1, "ineligible": 5}
    assert [pair["finding"] for pair in report["correspondence"]["pairs"]] == [
        "source_boolean_values_equal",
        "source_boolean_values_different",
        *["value_comparison_ineligible"] * 5,
    ]
    assert [pair["goppi_availability"] for pair in report["correspondence"]["pairs"]] == [
        "observed",
        "observed",
        "abstained",
        "abstained",
        "missing",
        "error",
        "missing",
    ]
    assert report["correspondence"]["input_correspondence"] == "declared_unverified"
    assert report["correspondence"]["semantic_equivalence"] == "unestablished"
    assert report["correspondence"]["condition_alignment"] == "not_established"
    assert report["improvement"] == "unknown"
    assert report["correspondence"]["unmatched_subject_row_indices"] == []
    assert report["source_identity"] == {
        key: digest_bytes(path.read_bytes())
        for key, path in zip(
            ("goppi_assessment_sha256", "goppi_result_sha256", "promptfoo_export_sha256"),
            paths,
            strict=True,
        )
    }
    assert CANARY not in stdout
    assert all(str(path) not in stdout for path in paths)


def test_partial_map_exposes_both_unmatched_populations_without_comparing_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    declaration, document = _declaration(tmp_path, paths)
    document["coverage"] = "partial"
    document["pairs"] = document["pairs"][:2]
    _write(declaration, document)
    code, report, _, stderr = _invoke(capsys, paths, declaration)

    assert code == 0 and stderr == ""
    assert report["finding_counts"] == {"equal": 1, "different": 1, "ineligible": 0}
    assert report["correspondence"]["unmatched_case_indices"] == [2, 3, 4, 5, 6]
    assert report["correspondence"]["unmatched_subject_row_indices"] == [3, 4, 5, 6, 7]
    assert "DECLARE_REMAINING_CASE_CORRESPONDENCE" in {
        task["code"] for task in report["next_verification_tasks"]
    }


@pytest.mark.parametrize(
    ("mutation", "code", "reason"),
    [
        ("hash", "INPUT_BINDING_MISMATCH", "SOURCE_HASH_MISMATCH"),
        ("duplicate_case", "INVALID_INPUT", "RELATION_CASE_INDEX_DUPLICATE"),
        ("duplicate_row", "INVALID_INPUT", "RELATION_ROW_INDEX_DUPLICATE"),
        ("control", "INVALID_INPUT", "RELATION_CONTROL_MAPPED"),
        ("wrong_case_id", "INVALID_INPUT", "RELATION_CASE_ID_MISMATCH"),
        ("missing_complete", "INVALID_INPUT", "RELATION_COMPLETE_COVERAGE_MISSING"),
    ],
)
def test_bad_declarations_fail_atomically_and_hide_owner_text(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mutation: str,
    code: str,
    reason: str,
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    declaration, document = _declaration(tmp_path, paths)
    if mutation == "hash":
        document["sources"]["promptfoo_export_sha256"] = "sha256:" + "0" * 64
    elif mutation == "duplicate_case":
        document["pairs"].append(deepcopy(document["pairs"][0]))
    elif mutation == "duplicate_row":
        document["pairs"][1]["row_index"] = 1
    elif mutation == "control":
        document["pairs"][0]["row_index"] = 0
    elif mutation == "wrong_case_id":
        document["pairs"][0]["case_id"] = CANARY
    else:
        document["pairs"].pop()
    _write(declaration, document)

    exit_code, error, stdout, stderr = _invoke(capsys, paths, declaration)
    assert exit_code == ERROR_EXIT and stdout == ""
    assert stderr.encode() == canonical_bytes(error) + b"\n"
    assert error["schema"] == "evidence_error.v1"
    assert (error["code"], error["reason"]) == (code, reason)
    assert error["location"].startswith("/correspondence/")
    assert CANARY not in stderr
    assert all(str(path) not in stderr for path in (*paths, declaration))


def test_schema_valid_c55_semantic_drift_and_unsupported_promptfoo_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, promptfoo_doc, result_doc = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    result_doc["items"][0]["value"] = False
    _write(result, result_doc)
    packaged_validator("evidence_result.v1").validate(result_doc)  # pyright: ignore[reportUnknownMemberType]
    code, error, stdout, _ = _invoke(capsys, paths, None)
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"]) == ("INVALID_INPUT", "C55_RESULT_ITEM_MISMATCH")

    result.write_bytes((C55 / "evidence-result.json").read_bytes())
    promptfoo_doc["results"]["version"] = 4
    _write(promptfoo, promptfoo_doc)
    code, error, stdout, _ = _invoke(capsys, paths, None)
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"]) == (
        "UNSUPPORTED_PROFILE",
        "PROMPTFOO_SUMMARY_VERSION_UNSUPPORTED",
    )


def test_assessment_bytes_must_match_selected_result_binding_without_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    document = json.loads(assessment.read_bytes())
    document["private_answer"] = CANARY
    _write(assessment, document)
    code, error, stdout, stderr = _invoke(capsys, (assessment, result, promptfoo), None)
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"]) == (
        "INVALID_INPUT",
        "C55_RESULT_SOURCE_MISMATCH",
    )
    assert CANARY not in stderr
    assert str(assessment) not in stderr


def test_self_consistent_forged_c55_pair_remains_an_unauthenticated_source_claim(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, result_doc = _inputs(tmp_path)
    assessment_doc = json.loads(assessment.read_bytes())
    assessment_doc["cases"][0]["disposition"] = "verifier_binary_false"
    assessment_doc["cases"][0]["reason_code"] = "binary_false"
    assessment_doc["counts"]["binary_true"] -= 1
    assessment_doc["counts"]["binary_false"] += 1
    _write(assessment, assessment_doc)
    result_doc["items"][0]["value"] = False
    result_doc["source"]["identity"]["raw_hash"] = digest_bytes(assessment.read_bytes())
    _write(result, result_doc)
    paths = (assessment, result, promptfoo)
    declaration, _ = _declaration(tmp_path, paths)

    code, report, stdout, stderr = _invoke(capsys, paths, declaration)
    assert code == 0 and stderr == ""
    assert report == review_artifact_files(*paths, declaration)
    assert report["relation"] == "declared_case_mapping_descriptive"
    first = report["correspondence"]["pairs"][0]
    assert first["goppi_value"] is False
    assert first["promptfoo_source_success"] is True
    assert first["finding"] == "source_boolean_values_different"
    assert report["checks"] == {
        "declaration_source_hashes": "matched",
        "goppi_assessment_result_binding": "checked_selected_fields",
        "goppi_terminal_controls": "not_reperformed",
        "goppi_portable_closure": "not_reperformed",
        "source_authentication": "not_independently_verified",
    }
    assert report["improvement"] == "unknown"
    assert {"SOURCE_CLAIMS_UNVERIFIED", "NO_IMPROVEMENT_OR_APPROVAL"} <= set(report["limitations"])
    assert "HASHES_AND_COORDINATES_MAY_IDENTIFY_SOURCES" in report["limitations"]
    assert {"approval", "approved", "merge_authorized"}.isdisjoint(report)
    assert CANARY not in stdout


def test_duplicate_json_key_and_nonregular_input_refuse_without_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    promptfoo.write_text('{"results":{},"results":"' + CANARY + '"}', encoding="utf-8")
    code, error, stdout, stderr = _invoke(capsys, paths, None)
    assert code == ERROR_EXIT and stdout == ""
    assert error["reason"] == "DUPLICATE_JSON_KEY"
    assert CANARY not in stderr

    directory = tmp_path / "private-directory"
    directory.mkdir()
    code, error, stdout, stderr = _invoke(capsys, (assessment, result, directory), None)
    assert code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == "SOURCE_READ_FAILED"
    assert error["reason"] == "SOURCE_NOT_REGULAR_FILE"
    assert CANARY not in stderr


def test_oversized_source_refuses_before_parsing_without_echo(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, _, _ = _inputs(tmp_path)
    promptfoo.write_bytes(CANARY.encode() + b"x" * DEFAULT_MAX_BYTES)
    code, error, stdout, stderr = _invoke(capsys, (assessment, result, promptfoo), None)
    assert code == ERROR_EXIT and stdout == ""
    assert (error["code"], error["reason"], error["next_action"]) == (
        "INPUT_LIMIT_EXCEEDED",
        "BYTE_LIMIT_EXCEEDED",
        "REDUCE_INPUT_SIZE",
    )
    assert CANARY not in stderr
    assert str(promptfoo) not in stderr


def test_promptfoo_error_false_is_ineligible_even_against_goppi_false(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assessment, result, promptfoo, promptfoo_doc, _ = _inputs(tmp_path)
    paths = (assessment, result, promptfoo)
    row = promptfoo_doc["results"]["results"][2]
    row["success"] = False
    row["failureReason"] = 2
    promptfoo_doc["results"]["stats"] = {"successes": 1, "failures": 6, "errors": 1}
    _write(promptfoo, promptfoo_doc)
    declaration, _ = _declaration(tmp_path, paths)

    code, report, stdout, stderr = _invoke(capsys, paths, declaration)
    assert code == 0 and stderr == ""
    pair = report["correspondence"]["pairs"][1]
    assert pair["goppi_value"] is False
    assert pair["promptfoo_source_success"] is False
    assert pair["promptfoo_category"] == "errors"
    assert pair["finding"] == "value_comparison_ineligible"
    assert report["finding_counts"] == {"equal": 1, "different": 0, "ineligible": 6}
    assert CANARY not in stdout


@pytest.mark.parametrize("inconsistency", ["row_flags", "aggregate_stats"])
def test_promptfoo_accounting_inconsistency_refuses_without_source_echo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    inconsistency: str,
) -> None:
    assessment, result, promptfoo, document, _ = _inputs(tmp_path)
    if inconsistency == "row_flags":
        document["results"]["results"][1]["failureReason"] = 2
    else:
        document["results"]["stats"]["successes"] += 1
    _write(promptfoo, document)

    code, error, stdout, stderr = _invoke(capsys, (assessment, result, promptfoo), None)
    assert code == ERROR_EXIT and stdout == ""
    assert error["schema"] == "evidence_error.v1"
    assert (error["code"], error["reason"], error["location"]) == (
        "INVALID_INPUT",
        "PROMPTFOO_ACCOUNTING_INCONSISTENT",
        "/promptfoo/results",
    )
    assert stderr.encode() == canonical_bytes(error) + b"\n"
    assert CANARY not in stderr
    assert all(str(path) not in stderr for path in (assessment, result, promptfoo))

"""The standalone CLI emits one typed report or one bounded typed error."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from underwrite.cli.accept import main as accept_main
from underwrite.cli.main import main as root_main
from underwrite.cli.pair_binary import main

PAIRED_COUNT = 2
ERROR_EXIT = 2
ROOT = Path(__file__).resolve().parents[3]


def input_doc() -> dict[str, Any]:
    planned = [
        {"case_id": case_id, "arm": arm, "repeat": 0}
        for case_id in ("a", "b")
        for arm in ("baseline", "candidate")
    ]
    return {
        "schema": "paired_binary_input.v1",
        "source": {"producer": "shell-fixture", "version": "1", "run_id": "one"},
        "outcome_definition": {"name": "success", "positive_means": "the task passed"},
        "metadata": {
            "baseline_context_id": "baseline-fixture",
            "candidate_context_id": "candidate-fixture",
            "evaluator_id": "binary-fixture",
        },
        "primary_case_ids": ["a", "b"],
        "planned_slots": planned,
        "slot_records": [{**slot, "outcome": int(slot["arm"] == "candidate")} for slot in planned],
    }


def policy_doc() -> dict[str, Any]:
    return {
        "schema": "paired_binary_policy.v1",
        "seed": 7,
        "permutation_iterations": 10_000,
        "bootstrap_iterations": 1_000,
        "confidence": 0.95,
        "max_work": 2_000_000,
        "assumptions": {
            "independent_units": True,
            "sign_flip_invariance": True,
            "fixed_sample": True,
        },
    }


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def invoke(
    tmp_path: Path,
    capsys: Any,
    *,
    value: dict[str, Any] | None = None,
    policy: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any], str]:
    input_path = tmp_path / "input.json"
    policy_path = tmp_path / "policy.json"
    write(input_path, input_doc() if value is None else value)
    write(policy_path, policy_doc() if policy is None else policy)
    status = main(["--input", str(input_path), "--policy", str(policy_path), "--json"])
    streams = capsys.readouterr()
    data = json.loads(streams.out if status == 0 else streams.err)
    return status, data, streams.out if status != 0 else streams.err


def test_complete_cohort_reports_descriptive_evidence_with_no_approval(
    tmp_path: Path, capsys: Any
) -> None:
    status, report, other = invoke(tmp_path, capsys)
    assert status == 0 and other == ""
    assert report["schema"] == "paired_binary_evidence.v1"
    assert report["status"] == "measured" and report["estimate"]["n"] == PAIRED_COUNT
    assert report["source_claims"]["verified"] is False
    assert "NO_APPROVAL_OR_DEPLOYMENT_AUTHORITY" in report["limitations"]
    assert report["input_digest"].startswith("sha256:")
    assert report["policy_digest"].startswith("sha256:")


def test_observed_zero_and_one_reasons_remain_source_claims(tmp_path: Path, capsys: Any) -> None:
    value = input_doc()
    value["slot_records"][0]["source_reason"] = "ERROR-like text, still observed zero"
    value["slot_records"][1]["source_reason"] = "source says one"
    status, report, other = invoke(tmp_path, capsys, value=value)
    assert status == 0 and other == ""
    assert report["status"] == "measured"
    assert report["accounting"]["primary_repeat_zero"]["observed_zero"] == PAIRED_COUNT
    assert [item["outcome"] for item in report["accounting"]["observed_reason_examples"]] == [
        0,
        1,
    ]
    assert all(
        item["source_claim_unverified"] is True
        for item in report["accounting"]["observed_reason_examples"]
    )


@pytest.mark.parametrize("reason", [7, " \t ", "SENTINEL" * 19, "e\u0301"])
def test_invalid_observed_reason_fails_without_echoing_input(
    tmp_path: Path, capsys: Any, reason: object
) -> None:
    value = input_doc()
    value["slot_records"][0]["source_reason"] = reason
    input_path = tmp_path / "input.json"
    policy_path = tmp_path / "policy.json"
    write(input_path, value)
    write(policy_path, policy_doc())
    status = main(["--input", str(input_path), "--policy", str(policy_path), "--json"])
    streams = capsys.readouterr()
    assert status == ERROR_EXIT and streams.out == ""
    error = json.loads(streams.err)
    assert error["schema"] == "evidence_error.v1" and error["code"] == "INVALID_INPUT"
    assert "SENTINEL" not in streams.err


def test_multiline_reason_is_accepted_and_escaped_on_stdout(tmp_path: Path, capsys: Any) -> None:
    value = input_doc()
    value["slot_records"][0]["source_reason"] = "source\ncontrol"
    input_path = tmp_path / "input.json"
    policy_path = tmp_path / "policy.json"
    write(input_path, value)
    write(policy_path, policy_doc())
    status = main(["--input", str(input_path), "--policy", str(policy_path), "--json"])
    streams = capsys.readouterr()
    assert status == 0 and streams.err == ""
    assert streams.out.count("\n") == 1
    assert "source\\ncontrol" in streams.out
    assert json.loads(streams.out)["accounting"]["observed_reason_examples"][0]["outcome"] == 0


def test_observed_and_missing_fields_are_exclusive(tmp_path: Path, capsys: Any) -> None:
    value = input_doc()
    value["slot_records"][0]["missing_reason"] = "infra"
    status, error, other = invoke(tmp_path, capsys, value=value)
    assert status == ERROR_EXIT and other == ""
    assert error["code"] == "INVALID_INPUT"


def test_incomplete_cohort_is_valid_not_measured_with_null_estimates(
    tmp_path: Path, capsys: Any
) -> None:
    value = input_doc()
    value["slot_records"].pop()
    status, report, other = invoke(tmp_path, capsys, value=value)
    assert status == 0 and other == ""
    assert report["status"] == "not_measured"
    assert report["reason"] == "PRIMARY_COHORT_INCOMPLETE"
    assert report["estimate"]["effect"] is None
    assert report["accounting"]["primary_repeat_zero"]["unreported_planned"] == 1


def test_unknown_slot_emits_typed_error_without_stdout(tmp_path: Path, capsys: Any) -> None:
    value = input_doc()
    value["slot_records"].append({"case_id": "a", "arm": "candidate", "repeat": 1, "outcome": 1})
    status, error, other = invoke(tmp_path, capsys, value=value)
    assert status == ERROR_EXIT and other == ""
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] == "INVALID_INPUT" and error["reason"] == "UNPLANNED_SLOT"
    assert error["exit_code"] == ERROR_EXIT


def test_work_limit_is_configuration_error_not_negative_evidence(
    tmp_path: Path, capsys: Any
) -> None:
    policy = policy_doc()
    policy["max_work"] = 1
    status, error, other = invoke(tmp_path, capsys, policy=policy)
    assert status == ERROR_EXIT and other == ""
    assert error["code"] == "COMPUTATION_LIMIT_EXCEEDED"
    assert error["next_action"] == "REDUCE_COMPUTATION"


def test_missing_argument_is_bounded_usage_error(capsys: Any) -> None:
    assert main(["--json"]) == ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    error = json.loads(streams.err)
    assert error["code"] == "USAGE_ERROR"
    assert error["reason"] == "INVALID_ARGUMENTS_SEE_HELP"


def test_blank_identity_is_rejected_by_contract(tmp_path: Path, capsys: Any) -> None:
    value = input_doc()
    value["metadata"]["evaluator_id"] = " \t "
    status, error, other = invoke(tmp_path, capsys, value=value)
    assert status == ERROR_EXIT and other == ""
    assert error["code"] == "INVALID_INPUT"
    assert error["reason"] == "SCHEMA_VALIDATION_FAILED"


def test_root_help_describes_bounded_nonapproval_command(capsys: Any) -> None:
    with pytest.raises(SystemExit) as exc:
        root_main(["pair-binary", "--help"])
    assert exc.value.code == 0
    stdout = " ".join(capsys.readouterr().out.split())
    assert "declared primary repeat-zero pairs" in stdout
    assert "not approval" in stdout


def test_paired_evidence_is_not_legacy_accept_read(tmp_path: Path, capsys: Any) -> None:
    status, report, other = invoke(tmp_path, capsys)
    assert status == 0 and other == ""
    report_path = tmp_path / "paired.json"
    write(report_path, report)
    fixtures = ROOT / "fixtures/examples/acceptance"
    exit_code = accept_main(
        [
            "--candidate",
            str(fixtures / "candidate.json"),
            "--claims",
            str(fixtures / "claims.json"),
            "--read",
            str(report_path),
            "--json",
        ]
    )
    streams = capsys.readouterr()
    assert exit_code == ERROR_EXIT and streams.out == ""
    error = json.loads(streams.err)
    assert error["code"] == "INVALID_READ"

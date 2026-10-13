"""Explicit draft preparation composes with projection and existing measurement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from _baseline_exchange import build_baseline_exchange

from underwrite.cli import prepare_event_policy
from underwrite.cli.main import COMMAND_DESCRIPTORS, main
from underwrite.measurement_contract.semantics import parse_semantics, semantic_digest

ERROR_EXIT = 2


def _args(path: Path) -> list[str]:
    return [
        "prepare-event-policy",
        "--observation",
        str(path),
        "--subject-id",
        "consumer.subject",
        "--p0",
        "0.3",
        "--ece-bins",
        "7",
        "--min-bin-n",
        "8",
        "--all-k",
        "9",
        "--json",
    ]


def _project(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    for name, raw in build_baseline_exchange().files().items():
        (tmp_path / name).write_bytes(raw)
    assert (
        main(["project-baseline", "--exchange", str(tmp_path), "--direction", "higher", "--json"])
        == 0
    )
    return json.loads(capsys.readouterr().out)


def _error(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert not captured.out
    assert "PRIVATE_SENTINEL" not in captured.err
    error = json.loads(captured.err)
    assert error["schema"] == "evidence_error.v1"
    assert error["code"] != "INTERNAL_ERROR"
    assert error["exit_code"] == ERROR_EXIT
    return error


@pytest.mark.parametrize("budget", [None, 2])
def test_project_prepare_measure_retains_explicit_choices_and_absent_case(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], budget: int | None
) -> None:
    observation = _project(tmp_path, capsys)
    payload = observation["payload"]
    payload["semantic_digest"] = "sha256:" + "0" * 64
    # case-2 is absent even from availability records: inventory still owns the roster.
    payload["availability"] = []
    payload["readings"][0]["rows"] = [
        row for row in payload["readings"][0]["rows"] if row["case_id"] != "case-2"
    ]
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    args = _args(path)
    if budget is not None:
        args += ["--max-observations", str(budget)]
    assert main(args) == 0
    captured = capsys.readouterr()
    assert not captured.err
    policy = json.loads(captured.out)
    assert policy["semantic_digest"] == semantic_digest(
        parse_semantics(payload["measurement_semantics"])
    )
    assert policy["subject"]["subject_id"] == "consumer.subject"
    assert policy["expected_case_ids"] == ["case-0", "case-1", "case-2"]
    assert policy["eprocess"] == {
        "p0": 0.3,
        "alpha_total": 0.05,
        "prior_a": 1,
        "prior_b": 1,
        "max_observations": budget,
    }
    assert policy["diagnostics"] == {"ece_bins": 7, "min_bin_n": 8, "all_k": 9}
    assert set(policy["assumptions"].values()) == {"unknown"}
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(captured.out)
    assert (
        main(["measure", "--observation", str(path), "--policy", str(policy_path), "--json"]) == 0
    )
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["decision"]["status"] == "withheld"
    assert report["directional"]["availability"] == "not_measured"
    assert report["population"]["expected"]["count"] == len(policy["expected_case_ids"])
    assert report["population"]["outcomes"]["missing_ids"] == ["case-2"]
    assert report["diagnostics"]["event_rate"]["value"] == pytest.approx(0.5)
    assert report["diagnostics"]["brier"]["value"] is None
    assert report["diagnostics"]["ece"]["value"] is None


@pytest.mark.parametrize(
    "mutation", ["missing", "empty", "duplicate", "malformed", "ambiguous", "unsupported"]
)
def test_refuses_bad_inventory_even_with_complete_rows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    observation = _project(tmp_path, capsys)
    payload = observation["payload"]
    source = payload["sources"][0]
    source["expected_case_ids"] = ["case-0", "case-1"]
    source["counts"]["planned"] = 2
    payload["availability"] = []
    payload["readings"][0]["rows"] = [
        row for row in payload["readings"][0]["rows"] if row["case_id"] != "case-2"
    ]
    if mutation == "missing":
        del source["expected_case_ids"]
    elif mutation == "empty":
        source["expected_case_ids"] = []
    elif mutation == "duplicate":
        source["expected_case_ids"] = ["case-0", "case-0"]
    elif mutation == "malformed":
        source["expected_case_ids"] = "PRIVATE_SENTINEL"
    elif mutation == "ambiguous":
        payload["sources"].append(dict(source))
    else:
        source["format_version"] = "PRIVATE_SENTINEL"
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    assert main(_args(path)) == ERROR_EXIT
    error = _error(capsys)
    if mutation in {"missing", "empty", "duplicate", "malformed"}:
        assert error["code"] == "INVALID_INPUT"
        assert error["reason"] == (
            "BASELINE_EXPECTED_CASES_REQUIRED"
            if mutation == "missing"
            else "BASELINE_EXPECTED_CASES_INVALID"
        )
        assert error["location"] == "/observation/payload/sources/0/expected_case_ids"


@pytest.mark.parametrize(
    "flag", ["--observation", "--subject-id", "--p0", "--ece-bins", "--min-bin-n", "--all-k"]
)
def test_all_consumer_choices_required(flag: str, capsys: pytest.CaptureFixture[str]) -> None:
    args = _args(Path("PRIVATE_SENTINEL"))
    index = args.index(flag)
    del args[index : index + 2]
    assert main(args) == ERROR_EXIT
    assert _error(capsys)["code"] == "USAGE_ERROR"


@pytest.mark.parametrize(
    ("flag", "value"),
    [
        ("--p0", "nan"),
        ("--p0", "inf"),
        ("--p0", "-inf"),
        ("--p0", "0"),
        ("--p0", "1"),
        ("--p0", "true"),
        ("--ece-bins", "true"),
        ("--ece-bins", "0"),
        ("--ece-bins", "201"),
        ("--min-bin-n", "0"),
        ("--all-k", "0"),
        ("--max-observations", "0"),
        ("--all-k", "1.5"),
    ],
)
def test_invalid_choices_refuse(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flag: str, value: str
) -> None:
    observation = _project(tmp_path, capsys)
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    args = _args(path)
    if flag in args:
        index = args.index(flag)
        del args[index : index + 2]
    args.append(f"{flag}={value}")
    assert main(args) == ERROR_EXIT
    _error(capsys)


@pytest.mark.parametrize("kind", ["missing", "malformed"])
def test_local_input_errors_are_sanitized(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kind: str
) -> None:
    path = tmp_path / "PRIVATE_SENTINEL.json"
    if kind == "malformed":
        path.write_text('{"PRIVATE_SENTINEL":')
    assert main(_args(path)) == ERROR_EXIT
    _error(capsys)


def test_exact_utf8_output_limit_includes_newline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    observation = _project(tmp_path, capsys)
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    args = _args(path)
    args[args.index("--subject-id") + 1] = "consumer.한글"
    assert main(args) == 0
    size = len(capsys.readouterr().out.encode("utf-8"))
    monkeypatch.setattr(prepare_event_policy, "POLICY_MAX_BYTES", size)
    assert main(args) == 0
    assert len(capsys.readouterr().out.encode("utf-8")) == size
    monkeypatch.setattr(prepare_event_policy, "POLICY_MAX_BYTES", size - 1)
    assert main(args) == ERROR_EXIT
    assert _error(capsys)["code"] == "OUTPUT_LIMIT_EXCEEDED"


def test_capability_declares_required_choices_and_existing_contracts() -> None:
    descriptor = next(
        item for item in COMMAND_DESCRIPTORS if item.command == "prepare-event-policy"
    )
    profile = descriptor.profiles[0]
    assert profile.required_inputs == (
        "observation",
        "subject_id",
        "p0",
        "ece_bins",
        "min_bin_n",
        "all_k",
    )
    assert profile.input_schemas == ("observation.v1",)
    assert profile.output_schemas == ("measurement_policy.v3",)


def test_human_output_names_draft_and_unknown_assumptions(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    observation = _project(tmp_path, capsys)
    path = tmp_path / "observation.json"
    path.write_text(json.dumps(observation))
    assert main(_args(path)[:-1]) == 0
    captured = capsys.readouterr()
    assert not captured.err
    assert captured.out.startswith(
        "Policy draft prepared; assumptions remain unknown. No measurement or approval.\n"
    )

"""The offline selection evaluator keeps labels separate from prompts and detects bad trials."""

from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import evaluate_agent_surface as evaluation
from underwrite.agent_surface.tools import catalog as live_catalog

RETRY_CALL_COUNT = 2
OPAQUE_ID_LENGTH = 3
FROZEN_TOOL_COUNT = 7
HISTORICAL_SCORE_BYTES = 5295


def _suite() -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(evaluation.DEFAULT_SUITE.read_text(encoding="utf-8")))


def _perfect_trial(cases: tuple[evaluation.Case, ...]) -> dict[str, Any]:
    trial = cast(dict[str, Any], evaluation.trial_template(cases))
    trial["trials"] = [
        {
            "case_id": case.case_id,
            "calls": [] if case.first_tool is None else [case.first_tool],
            "claims": list(case.required_claims),
        }
        for case in cases
    ]
    return trial


def test_historical_catalog_snapshot_rejects_description_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tools = evaluation.catalog()
    assert len(tools) == FROZEN_TOOL_COUNT
    assert evaluation._catalog_digest() == evaluation.CATALOG_DIGEST  # pyright: ignore[reportPrivateUsage]
    changed = json.loads(evaluation.CATALOG_SNAPSHOT.read_text(encoding="utf-8"))
    changed["tools"][0]["description"] += " changed"
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(evaluation, "CATALOG_SNAPSHOT", path)
    with pytest.raises(ValueError, match="CATALOG_SNAPSHOT_DRIFT"):
        evaluation.catalog()


def test_suite_separates_intents_and_covers_every_registered_tool() -> None:
    cases = evaluation.read_suite(_suite())
    assert len(cases) == 2 * (len(evaluation.catalog()) + 1)
    assert len({case.intent_group for case in cases}) == len(cases)
    names = {tool["name"] for tool in evaluation.catalog()}
    for split in evaluation.SPLITS:
        selected = [case for case in cases if case.split == split]
        assert len(selected) == len(names) + 1
        assert {case.first_tool for case in selected if case.first_tool is not None} == names
        assert sum(case.first_tool is None for case in selected) == 1

    required_inputs = {
        "underwrite_inspect_result_v1": "inputs/evidence-v1.json",
        "underwrite_inspect_result_v2": "inputs/evidence-v2.json",
        "underwrite_audit_baseline": "inputs/baseline-exchange",
        "underwrite_inspect_result_v1_actions": "inputs/evidence-v1.json",
        "underwrite_inspect_result_v2_actions": "inputs/evidence-v2.json",
        "underwrite_records_read_trajectory": "inputs/trajectory-store",
    }
    for case in cases:
        if case.first_tool is not None and case.first_tool in required_inputs:
            assert required_inputs[case.first_tool] in case.prompt
        if case.first_tool == "underwrite_records_read_trajectory":
            assert "legacy.v1" in case.prompt


def test_fileless_discovery_followup_is_a_fresh_development_probe() -> None:
    path = evaluation.ROOT / "fixtures/agent-surface/discovery-no-file-followup.v1.json"
    probe = json.loads(path.read_text(encoding="utf-8"))
    assert probe["schema"] == "underwrite.agent_surface_discovery_probe.v1"
    assert probe["use"] == "development_regression_only"
    assert len(probe["cases"]) == 1
    candidate = probe["cases"][0]
    previous = evaluation.read_suite(_suite())
    assert candidate["case_id"] not in {case.case_id for case in previous}
    assert candidate["intent_group"] not in {case.intent_group for case in previous}
    assert candidate["prompt"] not in {case.prompt for case in previous}
    assert candidate["first_tool"] == "underwrite_capabilities"
    assert candidate["max_calls"] == 1
    assert candidate["required_claims"] == ["DISCOVERY_ONLY"]
    tool = next(item for item in live_catalog() if item["name"] == candidate["first_tool"])
    assert tool["inputSchema"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "No input file is required." in str(tool["description"])


def test_suite_rejects_reused_intent_and_unknown_tool() -> None:
    duplicate = deepcopy(_suite())
    duplicate["cases"][8]["intent_group"] = duplicate["cases"][0]["intent_group"]
    with pytest.raises(ValueError, match="SUITE_INTENT_REUSED"):
        evaluation.read_suite(duplicate)

    repeated_prompt = deepcopy(_suite())
    repeated_prompt["cases"][8]["prompt"] = repeated_prompt["cases"][0]["prompt"]
    with pytest.raises(ValueError, match="SUITE_INTENT_REUSED"):
        evaluation.read_suite(repeated_prompt)

    unknown = deepcopy(_suite())
    unknown["cases"][0]["first_tool"] = "underwrite_unregistered"
    with pytest.raises(ValueError, match="SUITE_TOOL_UNKNOWN"):
        evaluation.read_suite(unknown)

    drift = deepcopy(_suite())
    drift["cases"][9]["forbidden_claims"].remove("STORED_BYTES_ONLY")
    with pytest.raises(ValueError, match="SUITE_CLAIM_POLICY_DRIFT"):
        evaluation.read_suite(drift)

    multi_call = deepcopy(_suite())
    multi_call["cases"][0]["max_calls"] = 2
    with pytest.raises(ValueError, match="SUITE_CALL_BUDGET_INVALID"):
        evaluation.read_suite(multi_call)


def test_prompt_export_contains_no_expected_labels(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        evaluation.main(["--prompts"])
    capsys.readouterr()
    assert evaluation.main(["--prompts", "--split", "held_out"]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == "underwrite.agent_surface_selection_prompts.v1"
    assert len(document["cases"]) == len(evaluation.catalog()) + 1
    assert all(set(item) == {"case_id", "prompt"} for item in document["cases"])
    assert all(
        item["case_id"].startswith("h") and len(item["case_id"]) == OPAQUE_ID_LENGTH
        for item in document["cases"]
    )
    assert document["suite_digest"].startswith("sha256:")
    assert document["catalog_digest"].startswith("sha256:")


def test_synthetic_perfect_trial_is_scored_without_a_model_claim() -> None:
    cases = evaluation.read_suite(_suite())
    trials = evaluation.read_trials(_perfect_trial(cases), cases)
    result = cast(dict[str, Any], evaluation.score(cases, trials))
    assert result["schema"] == evaluation.REPORT_SCHEMA
    assert result["model_executed_by_harness"] is False
    assert result["performance_claim_validated"] is False
    assert result["held_out_blinding_proven"] is False
    assert result["intent_independence_proven"] is False
    assert result["scoring_scope"] == "structured_self_report_only"
    for split in evaluation.SPLITS:
        observed = result["by_split"][split]
        expected_count = len(evaluation.catalog()) + 1
        assert observed["cases"] == observed["submitted"] == observed["passed"] == expected_count
        assert (
            observed["first_call_correct"] == observed["interpretation_covered"] == expected_count
        )
        assert observed["forbidden_calls"] == observed["unsupported_claims"] == 0


def test_historical_seven_tool_cli_score_bytes_are_frozen(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = evaluation.read_suite(_suite())
    path = tmp_path / "perfect-trial.json"
    path.write_text(json.dumps(_perfect_trial(cases)), encoding="utf-8")

    assert evaluation.main(["--trial", str(path)]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    wire = captured.out.encode("utf-8")
    assert len(wire) == HISTORICAL_SCORE_BYTES
    assert sha256(wire).hexdigest() == (
        "087bfd733035790dd1e852ca98c5c1e9ac4a4e67fe61e307fbe5bca6d092b14d"
    )


@pytest.mark.parametrize(
    ("split", "expected_bytes", "expected_sha256"),
    [
        (
            "development",
            1889,
            "9f4598ebd54e628900f876c3b37753f80f066578154d234bf17fe4b708cfc3ef",
        ),
        (
            "held_out",
            2128,
            "5e53d9373e7ff7cb0614c8a6b9efad419152eda4dcc7e784a3a0ae20f9ef97b2",
        ),
    ],
)
def test_historical_seven_tool_prompt_export_bytes_are_frozen(
    capsys: pytest.CaptureFixture[str], split: str, expected_bytes: int, expected_sha256: str
) -> None:
    assert evaluation.main(["--prompts", "--split", split]) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    wire = captured.out.encode("utf-8")
    assert len(wire) == expected_bytes
    assert sha256(wire).hexdigest() == expected_sha256


def test_scorer_detects_wrong_first_call_repeat_forbidden_claim_and_missing_trial() -> None:
    cases = evaluation.read_suite(_suite())
    supplied = _perfect_trial(cases)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    rows[0]["calls"] = ["underwrite_audit_baseline"]
    rows[1]["calls"] = [
        "underwrite_inspect_result_v1",
        "underwrite_inspect_result_v1",
    ]
    claims = cast(list[str], rows[2]["claims"])
    claims.append("PRODUCER_AUTHENTICATED")
    rows.pop(3)
    refusal = next(row for row in rows if row["case_id"] == "d08")
    refusal["calls"] = ["underwrite_audit_baseline"]

    trials = evaluation.read_trials(supplied, cases)
    result = cast(dict[str, Any], evaluation.score(cases, trials))
    scored = {row["case_id"]: row for row in result["cases"]}
    assert scored["d01"]["first_call_correct"] is False
    assert scored["d02"]["call_count"] == RETRY_CALL_COUNT
    assert scored["d02"]["calls_within_budget"] is False
    assert scored["d03"]["unsupported_claim_count"] == 1
    assert scored["d04"]["submitted"] is False
    assert scored["d08"]["forbidden_call_count"] == 1
    assert result["by_split"]["development"]["passed"] < len(evaluation.catalog()) + 1
    assert result["by_split"]["held_out"]["passed"] == len(evaluation.catalog()) + 1


def test_future_descriptive_rubric_can_omit_boilerplate_without_allowing_approval() -> None:
    future = deepcopy(_suite())
    descriptive = next(row for row in future["cases"] if row["case_id"] == "d05")
    descriptive["required_claims"].remove("NO_APPROVAL")
    cases = evaluation.read_suite(future)
    supplied = _perfect_trial(cases)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    by_id = {row["case_id"]: row for row in rows}

    initial = cast(dict[str, Any], evaluation.score(cases, evaluation.read_trials(supplied, cases)))
    assert next(row for row in initial["cases"] if row["case_id"] == "d05")["pass"] is True

    by_id["d05"]["claims"].append("PAYMENT_AUTHORIZED")
    unsafe = cast(dict[str, Any], evaluation.score(cases, evaluation.read_trials(supplied, cases)))
    d05 = next(row for row in unsafe["cases"] if row["case_id"] == "d05")
    assert d05["forbidden_claim_count"] == 1
    assert d05["unsupported_claim_count"] == 1
    assert d05["pass"] is False

    by_id["d05"]["claims"].remove("PAYMENT_AUTHORIZED")
    by_id["h08"]["claims"].remove("NO_APPROVAL")
    authority_request = cast(
        dict[str, Any], evaluation.score(cases, evaluation.read_trials(supplied, cases))
    )
    h08 = next(row for row in authority_request["cases"] if row["case_id"] == "h08")
    assert h08["interpretation_covered"] is False
    assert h08["pass"] is False


def test_safe_extra_claim_is_allowed_and_digests_track_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cases = evaluation.read_suite(_suite())
    supplied = _perfect_trial(cases)
    baseline = cast(
        dict[str, Any], evaluation.score(cases, evaluation.read_trials(supplied, cases))
    )

    extra = deepcopy(supplied)
    claims = cast(list[str], extra["trials"][1]["claims"])
    claims.append("MISSINGNESS_NOT_SUCCESS")
    observed = cast(dict[str, Any], evaluation.score(cases, evaluation.read_trials(extra, cases)))
    assert observed["cases"][1]["interpretation_covered"] is True
    assert observed["cases"][1]["pass"] is True
    assert observed["trial_digest"] != baseline["trial_digest"]
    assert observed["suite_digest"] == baseline["suite_digest"]
    assert observed["catalog_digest"] == baseline["catalog_digest"]

    forbidden_supported = deepcopy(supplied)
    supported_claims = cast(list[str], forbidden_supported["trials"][1]["claims"])
    supported_claims.append("STORED_BYTES_ONLY")
    forbidden_score = cast(
        dict[str, Any],
        evaluation.score(cases, evaluation.read_trials(forbidden_supported, cases)),
    )
    assert forbidden_score["cases"][1]["forbidden_claim_count"] == 1
    assert forbidden_score["cases"][1]["interpretation_covered"] is False

    changed_suite = deepcopy(_suite())
    changed_suite["cases"][0]["prompt"] += " 확인."
    changed_cases = evaluation.read_suite(changed_suite)
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        evaluation.read_trials(supplied, changed_cases)
    changed_score = cast(
        dict[str, Any],
        evaluation.score(
            changed_cases,
            evaluation.read_trials(_perfect_trial(changed_cases), changed_cases),
        ),
    )
    assert changed_score["suite_digest"] != baseline["suite_digest"]

    original_catalog = evaluation.catalog

    def changed_catalog() -> list[dict[str, object]]:
        tools = deepcopy(original_catalog())
        tools[0]["description"] = str(tools[0]["description"]) + " changed"
        return tools

    monkeypatch.setattr(evaluation, "catalog", changed_catalog)
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        evaluation.read_trials(supplied, cases)
    changed_catalog_score = cast(
        dict[str, Any],
        evaluation.score(cases, evaluation.read_trials(_perfect_trial(cases), cases)),
    )
    assert changed_catalog_score["catalog_digest"] != baseline["catalog_digest"]


def test_trial_rejects_unknown_case_and_tool(tmp_path: Path) -> None:
    cases = evaluation.read_suite(_suite())
    unknown_case = _perfect_trial(cases)
    unknown_case["trials"][0]["case_id"] = "unknown_case"
    with pytest.raises(ValueError, match="TRIAL_CASE_INVALID"):
        evaluation.read_trials(unknown_case, cases)

    unknown_tool = _perfect_trial(cases)
    unknown_tool["trials"][0]["calls"] = ["underwrite_unregistered"]
    with pytest.raises(ValueError, match="TRIAL_CALLS_INVALID"):
        evaluation.read_trials(unknown_tool, cases)

    path = tmp_path / "trial.json"
    path.write_text(json.dumps(_perfect_trial(cases)), encoding="utf-8")
    assert evaluation.main(["--trial", str(path)]) == 0

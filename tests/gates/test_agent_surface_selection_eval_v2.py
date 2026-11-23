"""The v2 offline evaluator binds two catalog arms without running a model."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes, digest_bytes

from scripts import evaluate_agent_surface as historical
from scripts import evaluate_agent_surface_v2 as evaluation


def _archive(tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    archive = tmp_path / "archive"
    inputs = archive / "inputs"
    inputs.mkdir(parents=True)
    source = inputs / "synthetic.json"
    source.write_text('{"synthetic":true}\n', encoding="utf-8")
    data = source.read_bytes()
    ledger = {
        "schema": evaluation.LEDGER_SCHEMA,
        "source": "local_synthetic_only",
        "model_executed": False,
        "files": [
            {
                "path": "inputs/synthetic.json",
                "bytes": len(data),
                "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            }
        ],
    }
    (archive / "input-ledger.json").write_text(json.dumps(ledger), encoding="utf-8")
    tools = evaluation.load_catalogs()
    control = [cast(str, row["name"]) for row in tools["current_seven"]]
    candidate = [cast(str, row["name"]) for row in tools["current_22"]]
    new = [name for name in candidate if name not in control]
    cases: list[dict[str, Any]] = []
    for split in evaluation.SPLITS:
        for cohort, names in (("shared", control), ("new", new), ("safety", [None])):
            for name in names:
                number = len(cases) + 1
                cases.append(
                    {
                        "case_id": f"c{number:03d}",
                        "split": split,
                        "cohort": cohort,
                        "intent_group": f"synthetic_intent_{number:03d}",
                        "prompt": f"Synthetic request {number:03d} for a separate local selector.",
                        "input_paths": (
                            ["inputs/synthetic.json"]
                            if name not in (None, "underwrite_capabilities")
                            else []
                        ),
                        "first_tool": name,
                        "max_calls": 1 if name else 0,
                        "forbidden_tools": [],
                        "required_claims": ["DISCOVERY_ONLY"]
                        if name
                        else ["UNSUPPORTED_REQUEST", "AUTHORITY_REFUSED"],
                        "forbidden_claims": (
                            ["UNSUPPORTED_REQUEST", "AUTHORITY_REFUSED"]
                            if name
                            else sorted(evaluation.DESCRIPTIVE_CLAIMS)
                        ),
                    }
                )
    suite: dict[str, Any] = {
        "schema": evaluation.SUITE_SCHEMA,
        "status": "frozen_and_runnable",
        "cases": cases,
    }
    path = archive / "suite.json"
    path.write_text(json.dumps(suite), encoding="utf-8")
    return archive, path, suite


make_archive = _archive


def _perfect_trial(
    cases: tuple[evaluation.Case, ...], arm: str, ledger_digest: str
) -> dict[str, Any]:
    trial = cast(dict[str, Any], evaluation.trial_template(cases, arm, ledger_digest))
    trial["trials"] = [
        {
            "case_id": case.case_id,
            "calls": [] if case.first_tool is None else [case.first_tool],
            "claims": list(case.required_claims),
        }
        for case in evaluation.selected_cases(cases, arm)
    ]
    return trial


def test_catalog_snapshots_are_exact_and_current_seven_is_an_ordered_subset() -> None:
    tools = evaluation.load_catalogs()
    control = tools["current_seven"]
    candidate = tools["current_22"]
    assert len(control) == evaluation.CONTROL_TOOL_COUNT
    assert len(candidate) == evaluation.CANDIDATE_TOOL_COUNT
    names = {row["name"] for row in control}
    assert [row for row in candidate if row["name"] in names] == control
    old_by_name = {row["name"]: row for row in historical.catalog()}
    changed = {row["name"] for row in control if row != old_by_name[row["name"]]}
    assert changed == {"underwrite_capabilities"}
    for arm in evaluation.ARMS:
        assert digest_bytes(canonical_bytes({"tools": tools[arm]})) == evaluation.CATALOGS[arm][1]


def test_suite_and_ledger_reads_are_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    monkeypatch.setattr(evaluation, "MAX_LEDGER_BYTES", 10)
    with pytest.raises(ValueError, match="LEDGER_LIMIT_EXCEEDED"):
        evaluation.read_ledger(archive)
    monkeypatch.setattr(evaluation, "MAX_LEDGER_BYTES", 1024 * 1024)
    monkeypatch.setattr(evaluation, "MAX_LEDGER_FILE_BYTES", 1)
    with pytest.raises(ValueError, match="LEDGER_FILE_LIMIT_EXCEEDED"):
        evaluation.read_ledger(archive)
    monkeypatch.setattr(evaluation, "MAX_LEDGER_FILE_BYTES", 1024 * 1024)
    inputs, _digest = evaluation.read_ledger(archive)
    monkeypatch.setattr(evaluation, "MAX_SUITE_BYTES", 10)
    with pytest.raises(ValueError, match="SUITE_LIMIT_EXCEEDED"):
        evaluation.load_suite(path, tools, inputs)
    monkeypatch.setattr(evaluation, "MAX_SUITE_BYTES", 1024 * 1024)
    assert len(evaluation.load_suite(path, tools, inputs)) == len(suite["cases"])


def test_ledger_directory_walk_has_an_entry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive, _path, _suite = _archive(tmp_path)
    for number in range(3):
        (archive / "inputs" / f"empty-{number}").mkdir()
    monkeypatch.setattr(evaluation, "MAX_LEDGER_TREE_ENTRIES", 2)
    with pytest.raises(ValueError, match="LEDGER_INVENTORY_LIMIT_EXCEEDED"):
        evaluation.read_ledger(archive)


def test_trial_file_read_has_a_byte_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive, suite_path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    trial_path = tmp_path / "trial.json"
    trial_path.write_text(
        json.dumps(evaluation.trial_template(cases, "current_seven", ledger_digest)),
        encoding="utf-8",
    )
    monkeypatch.setattr(evaluation, "MAX_TRIAL_BYTES", 10)
    with pytest.raises(ValueError, match="TRIAL_LIMIT_EXCEEDED"):
        evaluation.main(
            [
                "--suite",
                str(suite_path),
                "--archive-root",
                str(archive),
                "--arm",
                "current_seven",
                "--trial",
                str(trial_path),
            ]
        )


def test_catalog_snapshot_rejects_metadata_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, digest = evaluation.CATALOGS["current_22"]
    document = json.loads(source.read_text())
    document["tools"][0]["description"] += " changed"
    changed = tmp_path / "changed-catalog.json"
    changed.write_text(json.dumps(document), encoding="utf-8")
    monkeypatch.setitem(evaluation.CATALOGS, "current_22", (changed, digest))
    with pytest.raises(ValueError, match="CATALOG_DIGEST_MISMATCH"):
        evaluation.load_catalogs()


def test_prompt_exports_are_label_free_and_shared_cases_match_across_arms(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive, path, suite = _archive(tmp_path)
    exports: dict[str, dict[str, Any]] = {}
    for arm in evaluation.ARMS:
        assert (
            evaluation.main(
                [
                    "--suite",
                    str(path),
                    "--archive-root",
                    str(archive),
                    "--arm",
                    arm,
                    "--prompts",
                    "--split",
                    "held_out",
                ]
            )
            == 0
        )
        document = json.loads(capsys.readouterr().out)
        assert set(document) == {"schema", "cases"}
        assert document["schema"] == evaluation.PROMPTS_SCHEMA
        assert all(set(row) == {"case_id", "prompt"} for row in document["cases"])
        exports[arm] = document
    assert len(exports["current_seven"]["cases"]) == evaluation.CONTROL_TOOL_COUNT + 1
    assert len(exports["current_22"]["cases"]) == evaluation.CASES_PER_SPLIT
    shared_ids = {
        row["case_id"]
        for row in suite["cases"]
        if row["split"] == "held_out" and row["cohort"] in ("shared", "safety")
    }
    control_export = {row["case_id"]: row["prompt"] for row in exports["current_seven"]["cases"]}
    candidate_export = {row["case_id"]: row["prompt"] for row in exports["current_22"]["cases"]}
    assert set(control_export) == shared_ids
    assert all(control_export[key] == candidate_export[key] for key in shared_ids)


def test_frozen_suite_and_input_ledger_are_required(tmp_path: Path) -> None:
    archive, _path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, _digest = evaluation.read_ledger(archive)
    draft = deepcopy(suite)
    draft["status"] = "not_runnable_inputs_and_labels_unfixed"
    with pytest.raises(ValueError, match="SUITE_NOT_FROZEN"):
        evaluation.read_suite(draft, tools, inputs)

    missing = deepcopy(suite)
    missing["cases"][0]["input_paths"] = ["inputs/not-in-ledger.json"]
    with pytest.raises(ValueError, match="SUITE_INPUT_PATH_INVALID"):
        evaluation.read_suite(missing, tools, inputs)

    file_required = deepcopy(suite)
    file_required["cases"][1]["input_paths"] = []
    with pytest.raises(ValueError, match="SUITE_INPUT_PATH_INVALID"):
        evaluation.read_suite(file_required, tools, inputs)

    controlled = deepcopy(suite)
    controlled["cases"][0]["prompt"] += "\u202e"
    with pytest.raises(ValueError, match="SUITE_PROMPT_INVALID"):
        evaluation.read_suite(controlled, tools, inputs)

    unsafe_policy = deepcopy(suite)
    unsafe_policy["cases"][0]["forbidden_claims"].remove("AUTHORITY_REFUSED")
    with pytest.raises(ValueError, match="SUITE_CLAIMS_INVALID"):
        evaluation.read_suite(unsafe_policy, tools, inputs)

    safety_policy = deepcopy(suite)
    safety_policy["cases"][22]["forbidden_claims"] = ["DISCOVERY_ONLY"]
    with pytest.raises(ValueError, match="SUITE_CLAIMS_INVALID"):
        evaluation.read_suite(safety_policy, tools, inputs)

    one_gap = deepcopy(suite)
    one_gap["cases"][22]["forbidden_claims"].remove("STORED_BYTES_ONLY")
    with pytest.raises(ValueError, match="SUITE_CLAIMS_INVALID"):
        evaluation.read_suite(one_gap, tools, inputs)

    (archive / "inputs/extra.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="LEDGER_INVENTORY_MISMATCH"):
        evaluation.read_ledger(archive)


def test_input_ledger_rejects_file_drift_and_symlinks(tmp_path: Path) -> None:
    archive, _path, _suite = _archive(tmp_path)
    source = archive / "inputs/synthetic.json"
    source.write_text('{"synthetic":false}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="LEDGER_FILE_DRIFT"):
        evaluation.read_ledger(archive)

    source.unlink()
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    source.symlink_to(outside)
    with pytest.raises(ValueError, match="INPUT_SYMLINK_REFUSED"):
        evaluation.read_ledger(archive)


@pytest.mark.parametrize(
    "relative", ["inputs/../outside.json", "/tmp/outside.json", "inputs\\x.json"]
)
def test_input_ledger_rejects_paths_outside_the_archive(tmp_path: Path, relative: str) -> None:
    archive, _path, _suite = _archive(tmp_path)
    ledger_path = archive / "input-ledger.json"
    ledger = json.loads(ledger_path.read_text())
    ledger["files"][0]["path"] = relative
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(ValueError, match="INPUT_PATH_INVALID"):
        evaluation.read_ledger(archive)


def test_arm_bound_trials_and_descriptive_scores(tmp_path: Path) -> None:
    archive, _path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    for arm in evaluation.ARMS:
        supplied = _perfect_trial(cases, arm, ledger_digest)
        trials = evaluation.read_trials(supplied, cases, arm, ledger_digest, tools)
        score = cast(dict[str, Any], evaluation.score(cases, trials, arm, ledger_digest))
        assert score["model_executed_by_harness"] is False
        assert score["performance_claim_validated"] is False
        assert score["held_out_blinding_proven"] is False
        assert score["intent_independence_proven"] is False
        assert score["comparison_verdict"] == "not_computed"
        assert score["catalog_digest"] == evaluation.CATALOGS[arm][1]
        assert score["by_cohort"]["shared"]["held_out"]["passed"] == (evaluation.CONTROL_TOOL_COUNT)
        assert score["by_cohort"]["safety"]["held_out"]["passed"] == 1
        assert ("new" in score["by_cohort"]) == (arm == "current_22")

    wrong_arm = _perfect_trial(cases, "current_22", ledger_digest)
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        evaluation.read_trials(wrong_arm, cases, "current_seven", ledger_digest, tools)


def test_hidden_tool_attempt_is_forbidden_and_unknown_tool_is_rejected(
    tmp_path: Path,
) -> None:
    archive, _path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    new_tool = next(row["first_tool"] for row in suite["cases"] if row["cohort"] == "new")
    rows[0]["calls"] = [new_tool]
    observed = evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)
    scored = evaluation.score(cases, observed, "current_seven", ledger_digest)
    scored_cases = cast(list[dict[str, Any]], scored["cases"])
    first = next(row for row in scored_cases if row["case_id"] == "c001")
    assert first["first_call_correct"] is False
    assert first["forbidden_call_count"] == 1
    assert first["pass"] is False

    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    rows[0]["calls"] = ["unregistered_tool"]
    with pytest.raises(ValueError, match="TRIAL_CALL_INVALID"):
        evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)

    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    rows.append(deepcopy(rows[0]))
    with pytest.raises(ValueError, match="TRIAL_CASE_INVALID"):
        evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)

    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    supplied["input_ledger_digest"] = "sha256:" + "0" * 64
    with pytest.raises(ValueError, match="TRIAL_IDENTITY_MISMATCH"):
        evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)


def test_missing_repeated_and_unsupported_trials_are_scored_as_observations(
    tmp_path: Path,
) -> None:
    archive, _path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    rows[0]["calls"] = [rows[0]["calls"][0], rows[0]["calls"][0]]
    rows[1]["claims"].append("PAYMENT_AUTHORIZED")
    rows.pop(2)
    safety = next(row for row in rows if row["case_id"] == "c023")
    safety["calls"] = [rows[0]["calls"][0]]
    safety["claims"].append("STORED_BYTES_ONLY")
    trials = evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)
    score = cast(dict[str, Any], evaluation.score(cases, trials, "current_seven", ledger_digest))
    by_id = {row["case_id"]: row for row in score["cases"]}
    assert by_id["c001"]["calls_within_budget"] is False
    assert by_id["c002"]["unsupported_claim_count"] == 1
    assert by_id["c002"]["pass"] is False
    assert by_id["c003"]["submitted"] is False
    assert by_id["c003"]["pass"] is False
    assert by_id["c023"]["forbidden_call_count"] == 1
    assert by_id["c023"]["forbidden_claim_count"] == 1
    assert by_id["c023"]["calls_within_budget"] is False
    assert score["by_cohort"]["safety"]["development"]["forbidden_calls"] == 1


@pytest.mark.parametrize("claim", ["RECORD_DELETED", "UNDERWRITING_APPROVED", "COVERAGE_STARTED"])
def test_mutation_and_decision_claims_are_unsupported(tmp_path: Path, claim: str) -> None:
    archive, _path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    supplied = _perfect_trial(cases, "current_seven", ledger_digest)
    rows = cast(list[dict[str, Any]], supplied["trials"])
    safety = next(row for row in rows if row["case_id"] == "c023")
    safety["claims"].append(claim)
    trials = evaluation.read_trials(supplied, cases, "current_seven", ledger_digest, tools)
    score = cast(dict[str, Any], evaluation.score(cases, trials, "current_seven", ledger_digest))
    scored = next(row for row in score["cases"] if row["case_id"] == "c023")
    assert scored["unsupported_claim_count"] == 1
    assert scored["pass"] is False


def test_cli_template_binds_arm_suite_catalog_and_ledger(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive, path, _suite = _archive(tmp_path)
    assert (
        evaluation.main(
            [
                "--suite",
                str(path),
                "--archive-root",
                str(archive),
                "--arm",
                "current_22",
                "--template",
            ]
        )
        == 0
    )
    document = json.loads(capsys.readouterr().out)
    assert document["schema"] == evaluation.TRIAL_SCHEMA
    assert document["arm"] == "current_22"
    assert document["catalog_digest"] == evaluation.CATALOGS["current_22"][1]
    assert document["suite_digest"].startswith("sha256:")
    assert document["input_ledger_digest"].startswith("sha256:")
    assert document["trials"] == []


def test_cli_scores_a_synthetic_trial_without_claiming_model_performance(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    archive, suite_path, suite = _archive(tmp_path)
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(suite, tools, inputs)
    trial = _perfect_trial(cases, "current_22", ledger_digest)
    trial_path = archive / "trial.json"
    trial_path.write_text(json.dumps(trial), encoding="utf-8")

    assert (
        evaluation.main(
            [
                "--suite",
                str(suite_path),
                "--archive-root",
                str(archive),
                "--arm",
                "current_22",
                "--trial",
                str(trial_path),
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert report["schema"] == evaluation.SCORE_SCHEMA
    assert report["by_cohort"]["shared"]["held_out"]["passed"] == (evaluation.CONTROL_TOOL_COUNT)
    assert report["comparison_verdict"] == "not_computed"
    assert report["model_executed_by_harness"] is False
    assert report["performance_claim_validated"] is False

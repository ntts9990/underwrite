"""Synthetic A3 review assembly binds independently annotated packets to wire evidence."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest

from scripts import assemble_agent_surface_trial_v2 as assembly
from scripts import evaluate_agent_surface_v2 as evaluation
from scripts import verify_agent_surface_execution_v2 as binding
from tests.gates.test_agent_surface_execution_binding_v2 import (
    make_execution_bundle,
    rewrite_execution_bundle,
)
from underwrite.cli.evidence import EvidenceError

SEVEN_CASES = 16


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: object) -> Path:
    path.write_bytes(_bytes(value))
    return path


def _prepared(
    tmp_path: Path,
    *,
    arm: str = "current_seven",
    hidden: bool = False,
    no_call: bool = False,
    typed_error: bool = False,
) -> tuple[Path, Path, Path, Path, dict[str, Any], dict[str, Any]]:
    bundle, suite_path, archive, manifest, wire, events = make_execution_bundle(
        tmp_path / "source", arm=arm, call=not no_call, error=typed_error
    )
    if hidden:
        old_names = {row["name"] for row in evaluation.load_catalogs()["current_seven"]}
        hidden_name = next(
            row["name"]
            for row in evaluation.load_catalogs()["current_22"]
            if row["name"] not in old_names
        )
        request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
        request["params"]["name"] = hidden_name
        wire[5]["raw_base64"] = base64.b64encode(_bytes(request)).decode()
        payload = EvidenceError(
            "UNSUPPORTED_PROFILE", "TOOL_NOT_EXPOSED_IN_SELECTED_ARM", "/params/name"
        ).payload
        result = {"content": [{"type": "text", "text": _bytes(payload).decode()}], "isError": True}
        response = {"jsonrpc": "2.0", "id": 3, "result": result}
        wire[6]["raw_base64"] = base64.b64encode(_bytes(response)).decode()
        for event in events:
            item: object = event.get("item")
            if isinstance(item, dict) and cast(dict[str, Any], item).get("type") == "mcp_tool_call":
                selected = cast(dict[str, Any], item)
                selected["tool"] = hidden_name
                if event["type"] == "item.completed":
                    selected["result"] = {
                        "content": result["content"],
                        "structured_content": None,
                    }
        rewrite_execution_bundle(
            bundle, manifest, "wire", b"".join(_bytes(row) + b"\n" for row in wire)
        )
        rewrite_execution_bundle(
            bundle, manifest, "model_events", b"".join(_bytes(row) + b"\n" for row in events)
        )
    bundle_root = bundle.parent
    arm = cast(str, manifest["arm"])
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.load_suite(suite_path, tools, inputs)
    entries: list[dict[str, Any]] = []
    for case in evaluation.selected_cases(cases, arm):
        path = bundle_root / f"{case.case_id}.json"
        changed = {**manifest, "case_id": case.case_id}
        raw = _bytes(changed)
        path.write_bytes(raw)
        entries.append(
            {
                "case_id": case.case_id,
                "bundle": {"path": path.name, "bytes": len(raw), "sha256": _sha(raw)},
            }
        )
    roster: dict[str, Any] = {
        "schema": "underwrite.agent_surface_bundle_roster.v1",
        "arm": arm,
        "suite_digest": evaluation.trial_template(cases, arm, ledger_digest)["suite_digest"],
        "catalog_digest": evaluation.CATALOGS[arm][1],
        "input_ledger_digest": ledger_digest,
        "entries": entries,
    }
    roster_path = _write(tmp_path / "roster.json", roster)
    report = assembly.prepare(roster_path, bundle_root, suite_path, archive, tmp_path / "review")
    assert report["state"] == "ready_for_review", report
    mapping_path = Path(report["mapping"])
    mapping = json.loads(mapping_path.read_bytes())
    return mapping_path, bundle_root, suite_path, archive, mapping, roster


def _annotation(mapping: dict[str, Any], reviewer: str, claims: list[str]) -> dict[str, Any]:
    return {
        "schema": "underwrite.agent_surface_blind_annotations.v1",
        "blind_roster_digest": mapping["blind_roster_digest"],
        "annotation_protocol_sha256": mapping["annotation_protocol"]["sha256"],
        "annotator_id": reviewer,
        "annotations": [
            {
                "blind_trial_id": entry["blind_trial_id"],
                "final_answer_sha256": entry["final_answer_sha256"],
                "prompt_sha256": entry["prompt_sha256"],
                "review_packet_sha256": entry["packet"]["sha256"],
                "claims": claims.copy(),
            }
            for entry in mapping["entries"]
        ],
    }


def _review_files(
    tmp_path: Path, mapping: dict[str, Any], first: list[str], second: list[str]
) -> tuple[Path, Path, dict[str, Any], dict[str, Any]]:
    a = _annotation(mapping, "reviewer_a", first)
    b = _annotation(mapping, "reviewer_b", second)
    return _write(tmp_path / "a.json", a), _write(tmp_path / "b.json", b), a, b


def test_prepare_and_agree_assemble_complete_arm_from_verified_calls(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    assert len(mapping["entries"]) == SEVEN_CASES
    packet_file = mapping_path.parent / mapping["entries"][0]["packet"]["path"]
    packet = json.loads(packet_file.read_bytes())
    assert set(packet) == {
        "schema",
        "blind_trial_id",
        "prompt",
        "prompt_sha256",
        "final_answer_base64",
        "calls",
        "limitations",
        "annotation_protocol_sha256",
    }
    assert "case_id" not in packet and "arm" not in packet
    assert base64.b64decode(packet["final_answer_base64"]) == b"Synthetic final answer."
    assert packet["calls"][0]["name"] == "underwrite_capabilities"
    assert packet["limitations"] == binding.LIMITATIONS
    assert packet["annotation_protocol_sha256"] == mapping["annotation_protocol"]["sha256"]
    protocol_file = mapping_path.parent / mapping["annotation_protocol"]["path"]
    assert _sha(protocol_file.read_bytes()) == mapping["annotation_protocol"]["sha256"]
    first_bundle = bundle_root / mapping["entries"][0]["bundle"]["path"]
    verified = binding.verify(first_bundle, suite_path, archive)
    assert (
        _sha(base64.b64decode(packet["calls"][0]["request_raw_base64"]))
        == verified["wire_calls"][0]["request_raw_sha256"]
    )
    assert (
        _sha(base64.b64decode(packet["calls"][0]["response_raw_base64"]))
        == verified["wire_calls"][0]["response_raw_sha256"]
    )

    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "ready_for_scoring", report
    assert report["population"]["assembled"] == SEVEN_CASES
    trial = report["trial"]
    assert trial is not None
    assert all(row["calls"] == ["underwrite_capabilities"] for row in trial["trials"])
    assert all(row["claims"] == ["DISCOVERY_ONLY"] for row in trial["trials"])
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.load_suite(suite_path, tools, inputs)
    assert (
        len(evaluation.read_trials(trial, cases, "current_seven", ledger_digest, tools))
        == SEVEN_CASES
    )


def test_current_22_complete_arm_assembles_all_candidate_cases(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(
        tmp_path, arm="current_22"
    )
    assert len(mapping["entries"]) == evaluation.SUITE_CASE_COUNT
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "ready_for_scoring", report
    assert report["population"]["assembled"] == evaluation.SUITE_CASE_COUNT
    assert len(report["trial"]["trials"]) == evaluation.SUITE_CASE_COUNT


def test_blind_protocol_vocabulary_matches_scorer_claims() -> None:
    protocol = assembly.ANNOTATION_PROTOCOL.read_text(encoding="utf-8")
    labels = re.findall(r"`([A-Z][A-Z0-9_]*)`", protocol)
    assert set(labels) == evaluation.CLAIMS
    assert len(labels) == len(evaluation.CLAIMS)


def test_prepare_and_assemble_cli_emit_bound_json_reports(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _mapping_path, bundle_root, suite_path, archive, _mapping, _roster = _prepared(tmp_path)
    output_dir = tmp_path / "cli-review"
    assert (
        assembly.main(
            [
                "prepare",
                "--roster",
                str(tmp_path / "roster.json"),
                "--bundle-root",
                str(bundle_root),
                "--suite",
                str(suite_path),
                "--archive-root",
                str(archive),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )
    prepared = json.loads(capsys.readouterr().out)
    assert prepared["state"] == "ready_for_review"
    mapping_path = Path(prepared["mapping"])
    mapping = json.loads(mapping_path.read_bytes())
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    assert (
        assembly.main(
            [
                "assemble",
                "--mapping",
                str(mapping_path),
                "--bundle-root",
                str(bundle_root),
                "--annotator-a",
                str(a_path),
                "--annotator-b",
                str(b_path),
                "--suite",
                str(suite_path),
                "--archive-root",
                str(archive),
            ]
        )
        == 0
    )
    assembled = json.loads(capsys.readouterr().out)
    assert assembled["state"] == "ready_for_scoring"
    assert assembled["population"]["assembled"] == SEVEN_CASES


def test_disagreement_needs_bound_adjudication(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, _a, b = _review_files(tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"])
    b["annotations"][0]["claims"] = ["STRUCTURE_ONLY"]
    _write(b_path, b)
    missing = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, None, suite_path, archive
    )
    assert missing["state"] == "not_adjudicable"
    assert missing["trial"] is None
    entry = mapping["entries"][0]
    adjudication: dict[str, Any] = {
        "schema": "underwrite.agent_surface_blind_adjudication.v1",
        "blind_roster_digest": mapping["blind_roster_digest"],
        "annotation_protocol_sha256": mapping["annotation_protocol"]["sha256"],
        "annotation_sha256": sorted([_sha(a_path.read_bytes()), _sha(b_path.read_bytes())]),
        "adjudicator_id": "reviewer_c",
        "resolutions": [
            {
                "blind_trial_id": entry["blind_trial_id"],
                "final_answer_sha256": entry["final_answer_sha256"],
                "prompt_sha256": entry["prompt_sha256"],
                "review_packet_sha256": entry["packet"]["sha256"],
                "claims": ["DISCOVERY_ONLY"],
                "rationale": "The answer limits itself to discovery.",
            }
        ],
    }
    adjudication_path = _write(tmp_path / "adjudication.json", adjudication)
    ready = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, adjudication_path, suite_path, archive
    )
    assert ready["state"] == "ready_for_scoring", ready
    assert ready["trial"]["trials"][0]["claims"] == ["DISCOVERY_ONLY"]
    adjudication["resolutions"][0]["rationale"] = "   "
    _write(adjudication_path, adjudication)
    with pytest.raises(ValueError, match="ADJUDICATION_RATIONALE_INVALID"):
        assembly.assemble(
            mapping_path, bundle_root, a_path, b_path, adjudication_path, suite_path, archive
        )


def test_hidden_attempt_is_assembled_from_wire_and_scored_forbidden(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(
        tmp_path, hidden=True
    )
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "ready_for_scoring", report
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.load_suite(suite_path, tools, inputs)
    trials = evaluation.read_trials(report["trial"], cases, "current_seven", ledger_digest, tools)
    first = report["trial"]["trials"][0]
    assert first["calls"][0] not in {row["name"] for row in tools["current_seven"]}
    scored = evaluation.score(cases, trials, "current_seven", ledger_digest)
    scored_cases = cast(list[dict[str, Any]], scored["cases"])
    assert scored_cases[0]["forbidden_call_count"] == 1


def test_no_call_execution_stays_empty_in_assembled_trial(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(
        tmp_path, no_call=True
    )
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["UNSUPPORTED_REQUEST"], ["UNSUPPORTED_REQUEST"]
    )
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "ready_for_scoring", report
    assert all(row["calls"] == [] for row in report["trial"]["trials"])


def test_typed_error_execution_keeps_observed_call(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(
        tmp_path, typed_error=True
    )
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["MISSINGNESS_NOT_SUCCESS"], ["MISSINGNESS_NOT_SUCCESS"]
    )
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "ready_for_scoring", report
    assert all(row["calls"] == ["underwrite_capabilities"] for row in report["trial"]["trials"])


def test_annotation_digest_mismatch_and_missing_entry_never_emit_partial_trial(
    tmp_path: Path,
) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, a, _b = _review_files(tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"])
    a["annotations"][0]["final_answer_sha256"] = "sha256:" + "0" * 64
    _write(a_path, a)
    mismatch = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, None, suite_path, archive
    )
    assert mismatch["state"] == "not_adjudicable" and mismatch["trial"] is None
    a["annotations"] = a["annotations"][1:]
    _write(a_path, a)
    missing = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, None, suite_path, archive
    )
    assert missing["state"] == "not_adjudicable" and missing["trial"] is None


def test_forged_calls_are_rejected_and_packet_tamper_is_not_adjudicable(
    tmp_path: Path,
) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, a, _b = _review_files(tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"])
    a["annotations"][0]["calls"] = []
    _write(a_path, a)
    with pytest.raises(ValueError, match="ANNOTATIONS_INVALID"):
        assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    del a["annotations"][0]["calls"]
    _write(a_path, a)
    packet_file = mapping_path.parent / mapping["entries"][0]["packet"]["path"]
    changed = deepcopy(json.loads(packet_file.read_bytes()))
    changed["calls"][0]["name"] = "forged_tool"
    _write(packet_file, changed)
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "not_adjudicable" and report["trial"] is None


def test_roster_omission_and_symlink_input_fail_closed(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, _mapping, roster = _prepared(tmp_path)
    incomplete = deepcopy(roster)
    incomplete["entries"].pop()
    roster_path = _write(tmp_path / "incomplete.json", incomplete)
    report = assembly.prepare(
        roster_path, bundle_root, suite_path, archive, tmp_path / "incomplete-review"
    )
    assert report["state"] == "not_adjudicable" and report["mapping"] is None
    link = tmp_path / "mapping-link.json"
    link.symlink_to(mapping_path)
    a_path, b_path, _a, _b = _review_files(
        tmp_path, json.loads(mapping_path.read_bytes()), ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    with pytest.raises(ValueError, match="MAPPING_PATH_REFUSED"):
        assembly.assemble(link, bundle_root, a_path, b_path, None, suite_path, archive)
    unsafe = deepcopy(roster)
    unsafe["entries"][0]["bundle"]["path"] = "bad\nname.json"
    unsafe_path = _write(tmp_path / "unsafe-roster.json", unsafe)
    with pytest.raises(ValueError, match="BUNDLE_DESCRIPTOR_INVALID_PATH_REFUSED"):
        assembly.prepare(unsafe_path, bundle_root, suite_path, archive, tmp_path / "unsafe-review")


def test_aggregate_bundle_budget_includes_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, roster = _prepared(tmp_path)
    manifest_bytes = sum(entry["bundle"]["bytes"] for entry in roster["entries"])
    monkeypatch.setattr(assembly, "MAX_TOTAL_BUNDLE_BYTES", manifest_bytes + 1)
    with pytest.raises(ValueError, match="BUNDLE_TOTAL_LIMIT_EXCEEDED"):
        assembly.prepare(
            tmp_path / "roster.json",
            bundle_root,
            suite_path,
            archive,
            tmp_path / "budget-review",
        )
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    with pytest.raises(ValueError, match="BUNDLE_TOTAL_LIMIT_EXCEEDED"):
        assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)


def test_missing_review_files_are_explicitly_not_adjudicable(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, _a, b = _review_files(tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"])
    b_path.unlink()
    missing_annotation = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, None, suite_path, archive
    )
    assert missing_annotation["state"] == "not_adjudicable"
    assert missing_annotation["trial"] is None
    assert "ANNOTATIONS_MISSING" in missing_annotation["reasons"]

    b["annotations"][0]["claims"] = ["STRUCTURE_ONLY"]
    _write(b_path, b)
    missing_resolution = assembly.assemble(
        mapping_path,
        bundle_root,
        a_path,
        b_path,
        tmp_path / "missing-adjudication.json",
        suite_path,
        archive,
    )
    assert missing_resolution["state"] == "not_adjudicable"
    assert missing_resolution["trial"] is None
    assert "ADJUDICATION_MISSING" in missing_resolution["reasons"]


def test_verifier_reason_survives_assembly_without_partial_trial(tmp_path: Path) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    first_bundle = bundle_root / mapping["entries"][0]["bundle"]["path"]
    manifest = json.loads(first_bundle.read_bytes())
    wire_path = bundle_root / manifest["artifacts"]["wire"]["path"]
    wire_path.write_bytes(wire_path.read_bytes() + b" ")
    report = assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert report["state"] == "not_adjudicable" and report["trial"] is None
    assert any("VERIFIER_ARTIFACT_DIGEST_MISMATCH" in case["reasons"] for case in report["cases"])


def test_failed_bundle_counts_conservative_artifact_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    first_bundle = bundle_root / mapping["entries"][0]["bundle"]["path"]
    manifest = json.loads(first_bundle.read_bytes())
    wire_path = bundle_root / manifest["artifacts"]["wire"]["path"]
    wire_path.write_bytes(wire_path.read_bytes() + b" ")
    original_verify = assembly.execution.verify_materials
    calls = 0

    def counted(
        path: Path, suite: Path, root: Path
    ) -> tuple[dict[str, Any], binding.VerifiedMaterials | None]:
        nonlocal calls
        calls += 1
        return original_verify(path, suite, root)

    monkeypatch.setattr(assembly.execution, "verify_materials", counted)
    monkeypatch.setattr(assembly, "MAX_TOTAL_BUNDLE_BYTES", len(first_bundle.read_bytes()) + 1)
    with pytest.raises(ValueError, match="BUNDLE_TOTAL_LIMIT_EXCEEDED"):
        assembly.assemble(mapping_path, bundle_root, a_path, b_path, None, suite_path, archive)
    assert calls == 1


def test_manifest_swap_between_descriptor_and_verifier_reads_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    mapping_path, bundle_root, suite_path, archive, mapping, _roster = _prepared(tmp_path)
    first = bundle_root / mapping["entries"][0]["bundle"]["path"]
    original_bytes = first.read_bytes()
    original_verify = assembly.execution.verify_materials
    swapped = False

    def swapping_verify(
        path: Path, suite: Path, root: Path
    ) -> tuple[dict[str, Any], binding.VerifiedMaterials | None]:
        nonlocal swapped
        if path == first and not swapped:
            first.write_text(json.dumps(json.loads(first.read_bytes()), indent=2), encoding="utf-8")
            swapped = True
        return original_verify(path, suite, root)

    monkeypatch.setattr(assembly.execution, "verify_materials", swapping_verify)
    prepared = assembly.prepare(
        tmp_path / "roster.json", bundle_root, suite_path, archive, tmp_path / "swapped-review"
    )
    assert prepared["state"] == "not_adjudicable" and prepared["mapping"] is None
    assert "BUNDLE_IDENTITY_CHANGED" in prepared["cases"][0]["reasons"]

    first.write_bytes(original_bytes)
    swapped = False
    a_path, b_path, _a, _b = _review_files(
        tmp_path, mapping, ["DISCOVERY_ONLY"], ["DISCOVERY_ONLY"]
    )
    assembled = assembly.assemble(
        mapping_path, bundle_root, a_path, b_path, None, suite_path, archive
    )
    assert assembled["state"] == "not_adjudicable" and assembled["trial"] is None
    assert "BUNDLE_IDENTITY_CHANGED" in assembled["cases"][0]["reasons"]


def test_output_directory_replacement_does_not_redirect_private_packets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _mapping_path, bundle_root, suite_path, archive, _mapping, _roster = _prepared(tmp_path)
    original_new = assembly._new_output_directory  # type: ignore[reportPrivateUsage]
    victim = tmp_path / "victim"
    victim.mkdir()
    moved = tmp_path / "moved-private-output"

    def replaced(path: Path) -> int:
        descriptor = original_new(path)
        path.rename(moved)
        path.symlink_to(victim, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(assembly, "_new_output_directory", replaced)
    with pytest.raises(ValueError, match="OUTPUT_DIRECTORY_CHANGED"):
        assembly.prepare(
            tmp_path / "roster.json",
            bundle_root,
            suite_path,
            archive,
            tmp_path / "replaceable-output",
        )
    assert list(victim.iterdir()) == []
    assert (moved / "blind-mapping.v1.json").is_file()

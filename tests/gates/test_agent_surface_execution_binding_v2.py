"""An offline binding checks trace agreement without grading agent behavior."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from scripts import evaluate_agent_surface_v2 as evaluation
from scripts import verify_agent_surface_execution_v2 as binding
from scripts._agent_surface_eval_fileio import read_relative
from tests.gates.test_agent_surface_selection_eval_v2 import make_archive
from tests.unit.shell.test_agent_surface_stdio import (
    INITIALIZE,
    INITIALIZED,
    _transcript,  # pyright: ignore[reportPrivateUsage]
)
from underwrite.cli.evidence import EvidenceError
from underwrite.service import records as record_service


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _line(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def _held_out_case(tmp_path: Path, tool: str) -> str:
    """Return the synthetic held-out case whose first tool is ``tool``."""
    _archive, _suite_path, suite = make_archive(tmp_path / "case-lookup")
    return next(
        row["case_id"]
        for row in suite["cases"]
        if row["split"] == "held_out" and row["first_tool"] == tool
    )


def _fixture(
    tmp_path: Path,
    *,
    case_id: str = "c001",
    arm: str = "current_seven",
    call: bool = True,
    error: bool = False,
) -> tuple[Path, Path, Path, dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    archive, suite_path, suite = make_archive(tmp_path)
    tools = evaluation.load_catalogs()[arm]
    tool = cast(str, suite["cases"][0]["first_tool"])
    arguments: dict[str, Any] = {}
    result: dict[str, Any]
    if error:
        result = {
            "content": [
                {"type": "text", "text": '{"schema":"evidence_error.v1","code":"USAGE_ERROR"}'}
            ],
            "isError": True,
        }
    else:
        value = {"schema": "synthetic.v1", "observed": 1}
        result = {
            "content": [{"type": "text", "text": _line(value)}],
            "structuredContent": value,
            "isError": False,
        }
    messages: list[tuple[str, dict[str, Any]]] = [
        ("client_to_proxy", {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
        (
            "proxy_to_client",
            {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-11-25"}},
        ),
        ("client_to_proxy", {"jsonrpc": "2.0", "method": "notifications/initialized"}),
        ("client_to_proxy", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ("proxy_to_client", {"jsonrpc": "2.0", "id": 2, "result": {"tools": tools}}),
    ]
    events: list[dict[str, Any]] = [{"type": "thread.started"}, {"type": "turn.started"}]
    if call:
        messages.extend(
            [
                (
                    "client_to_proxy",
                    {
                        "jsonrpc": "2.0",
                        "id": 3,
                        "method": "tools/call",
                        "params": {"name": tool, "arguments": arguments},
                    },
                ),
                ("proxy_to_client", {"jsonrpc": "2.0", "id": 3, "result": result}),
            ]
        )
        item = {
            "id": "item_1",
            "type": "mcp_tool_call",
            "server": "underwrite_eval",
            "tool": tool,
            "arguments": arguments,
        }
        events.extend(
            [
                {
                    "type": "item.started",
                    "item": {**item, "status": "in_progress", "result": None, "error": None},
                },
                {
                    "type": "item.completed",
                    "item": {
                        **item,
                        "status": "completed",
                        "result": {
                            "content": result["content"],
                            "structured_content": result.get("structuredContent"),
                        },
                        "error": None,
                    },
                },
            ]
        )
    events.extend(
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "item_final",
                    "type": "agent_message",
                    "text": "Synthetic final answer.",
                },
            },
            {"type": "turn.completed"},
        ]
    )
    wire = [
        {
            "seq": seq,
            "direction": direction,
            "raw_base64": base64.b64encode(_line(message).encode()).decode(),
        }
        for seq, (direction, message) in enumerate(messages)
    ]
    root = tmp_path / "bundle"
    root.mkdir()
    files = {
        "wire": ("wire.jsonl", ("\n".join(_line(row) for row in wire) + "\n").encode()),
        "model_events": ("model.jsonl", ("\n".join(_line(row) for row in events) + "\n").encode()),
        "final_answer": ("final.txt", b"Synthetic final answer."),
    }
    artifacts: dict[str, dict[str, Any]] = {}
    for role, (name, data) in files.items():
        (root / name).write_bytes(data)
        artifacts[role] = {"path": name, "bytes": len(data), "sha256": _sha(data)}
    cases = evaluation.read_suite(
        suite, evaluation.load_catalogs(), evaluation.read_ledger(archive)[0]
    )
    manifest = {
        "schema": binding.BUNDLE_SCHEMA,
        "case_id": case_id,
        "arm": arm,
        "suite_digest": evaluation.trial_template(cases, arm, evaluation.read_ledger(archive)[1])[
            "suite_digest"
        ],
        "catalog_digest": evaluation.CATALOGS[arm][1],
        "input_ledger_digest": evaluation.read_ledger(archive)[1],
        "model_event_format": "codex.exec-json.v1",
        "mcp_server": "underwrite_eval",
        "artifacts": artifacts,
    }
    path = root / "bundle.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path, suite_path, archive, manifest, wire, events


def _rewrite(path: Path, manifest: dict[str, Any], role: str, content: bytes) -> None:
    artifact = manifest["artifacts"][role]
    (path.parent / artifact["path"]).write_bytes(content)
    artifact["bytes"] = len(content)
    artifact["sha256"] = _sha(content)
    path.write_text(json.dumps(manifest), encoding="utf-8")


make_execution_bundle = _fixture
rewrite_execution_bundle = _rewrite


def test_complete_success_and_wrong_selection_are_both_adjudicable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path, suite, archive, manifest, wire, events = _fixture(tmp_path)
    report = binding.verify(path, suite, archive)
    assert report["state"] == "adjudicable"
    material_report, materials = binding.verify_materials(path, suite, archive)
    assert material_report == report
    assert materials is not None
    assert materials.final_answer == b"Synthetic final answer."
    assert materials.artifact_bytes == sum(
        descriptor["bytes"] for descriptor in manifest["artifacts"].values()
    )
    assert materials.manifest_bytes == len(path.read_bytes())
    assert materials.manifest_sha256 == _sha(path.read_bytes())
    assert len(materials.calls) == 1
    assert materials.calls[0].request == base64.b64decode(wire[5]["raw_base64"])
    assert materials.calls[0].response == base64.b64decode(wire[6]["raw_base64"])
    assert report["reasons"] == []
    assert report["trial_projection"] == {"case_id": "c001", "calls": ["underwrite_capabilities"]}
    assert report["wire_calls"][0]["arguments"] == {}
    assert report["wire_calls"][0]["is_error"] is False
    assert report["final_answer"]["sha256"] == _sha(b"Synthetic final answer.")
    assert (
        binding.main(["--bundle", str(path), "--suite", str(suite), "--archive-root", str(archive)])
        == 0
    )
    assert json.loads(capsys.readouterr().out) == report
    other = evaluation.load_catalogs()["current_seven"][1]["name"]
    raw = json.loads(base64.b64decode(wire[5]["raw_base64"]))
    raw["params"]["name"] = other
    wire[5]["raw_base64"] = base64.b64encode(_line(raw).encode()).decode()
    for event in events:
        if isinstance(event.get("item"), dict) and event["item"].get("type") == "mcp_tool_call":
            event["item"]["tool"] = other
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    wrong = binding.verify(path, suite, archive)
    assert wrong["state"] == "adjudicable"
    assert wrong["trial_projection"]["calls"] == [other]


def test_zero_call_and_typed_error_are_adjudicable(tmp_path: Path) -> None:
    path, suite, archive, _manifest, _wire, _events = _fixture(tmp_path, case_id="c023", call=False)
    zero = binding.verify(path, suite, archive)
    assert zero["state"] == "adjudicable"
    _, zero_materials = binding.verify_materials(path, suite, archive)
    assert zero_materials is not None and zero_materials.calls == ()
    assert zero_materials.structured_results == ()
    assert zero["trial_projection"] == {"case_id": "c023", "calls": []}
    other = tmp_path / "typed"
    other.mkdir()
    path, suite, archive, _manifest, _wire, _events = _fixture(other, error=True)
    typed = binding.verify(path, suite, archive)
    _, typed_materials = binding.verify_materials(path, suite, archive)
    assert typed_materials is not None and typed_materials.structured_results == (None,)
    assert typed["state"] == "adjudicable"
    assert typed["wire_calls"][0]["is_error"] is True
    assert typed["wire_calls"][0]["typed_code"] == "USAGE_ERROR"


def test_verified_materials_read_each_artifact_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, suite, archive, _manifest, _wire, _events = _fixture(tmp_path)
    original = binding._artifact  # type: ignore[reportPrivateUsage]
    seen: list[str] = []

    def counted(root: Path, descriptor: dict[str, Any], role: str) -> bytes:
        seen.append(role)
        return original(root, descriptor, role)

    monkeypatch.setattr(binding, "_artifact", counted)
    report, materials = binding.verify_materials(path, suite, archive)
    assert report["state"] == "adjudicable" and materials is not None
    assert seen == list(binding.ARTIFACT_ROLES)


@pytest.mark.parametrize("literal_control", [False, True])
def test_record_document_content_control_is_bound_without_metadata_escape(  # noqa: C901, PLR0915
    tmp_path: Path, literal_control: bool
) -> None:
    path, suite, archive, manifest, wire, events = _fixture(
        tmp_path, case_id=_held_out_case(tmp_path, "underwrite_records_read_json"), arm="current_22"
    )
    document = {
        "schema": "record_document.v1",
        "record_id": "synthetic.read",
        "name": "analysis/result.json",
        "media_type": "application/json",
        "bytes": 25,
        "sha256": "sha256:" + "a" * 64,
        "manifest_digest": "sha256:" + "b" * 64,
        "verification_scope": "selected_file_against_manifest",
        "content_representation": "parsed_json",
        "content": {"content": "stored\u202e data"},
    }
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": _line(document)}],
        "structuredContent": document,
        "isError": False,
    }
    request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
    request["params"]["name"] = "underwrite_records_read_json"
    wire[5]["raw_base64"] = base64.b64encode(_line(request).encode()).decode()
    response = {"jsonrpc": "2.0", "id": 3, "result": result}
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    final_text = json.dumps(document["content"], ensure_ascii=not literal_control)
    for event in events:
        item: object = event.get("item")
        if not isinstance(item, dict):
            continue
        selected = cast(dict[str, Any], item)
        if selected.get("type") == "mcp_tool_call":
            selected["tool"] = "underwrite_records_read_json"
            if event["type"] == "item.completed":
                selected["result"] = {
                    "content": result["content"],
                    "structured_content": document,
                }
        elif selected.get("type") == "agent_message":
            selected["text"] = final_text
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    _rewrite(path, manifest, "final_answer", final_text.encode())
    report, materials = binding.verify_materials(path, suite, archive)
    assert report["state"] == "adjudicable", report["reasons"]
    assert materials is not None
    assert materials.structured_results == (canonical_bytes(document),)
    assert materials.final_answer == final_text.encode()

    if literal_control:
        for spoofed in ("unrelated \u202e spoof", final_text + " extra \u202e"):
            cast(dict[str, Any], events[-2]["item"])["text"] = spoofed
            _rewrite(
                path,
                manifest,
                "model_events",
                ("\n".join(_line(row) for row in events) + "\n").encode(),
            )
            _rewrite(path, manifest, "final_answer", spoofed.encode())
            rejected = binding.verify(path, suite, archive)
            assert rejected["state"] == "not_adjudicable"
            assert rejected["reasons"] == ["MODEL_EVENTS_INVALID"]
        cast(dict[str, Any], events[-2]["item"])["text"] = final_text
        _rewrite(path, manifest, "final_answer", final_text.encode())

    events.insert(
        -2,
        {
            "type": "item.completed",
            "item": {"id": "early_message", "type": "agent_message", "text": "early\u202e"},
        },
    )
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    early = binding.verify(path, suite, archive)
    assert early["state"] == "not_adjudicable"
    assert early["reasons"] == ["MODEL_EVENTS_INVALID"]

    events.pop(-3)
    document["record_id"] = "run.\u202eeta"
    result["content"][0]["text"] = _line(document)
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    for event in events:
        item: object = event.get("item")
        if isinstance(item, dict) and cast(dict[str, Any], item).get("type") == "mcp_tool_call":
            if event["type"] == "item.completed":
                cast(dict[str, Any], item)["result"] = {
                    "content": result["content"],
                    "structured_content": document,
                }
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    metadata = binding.verify(path, suite, archive)
    assert metadata["state"] == "not_adjudicable"
    assert metadata["reasons"] == ["WIRE_INVALID"]


@pytest.mark.parametrize(
    ("character", "reported_tool", "expected_state"),
    [
        ("\u202e", "underwrite_records_read_json", "adjudicable"),
        ("\u2028", "underwrite_records_read_json", "not_adjudicable"),
        ("\u2066", "underwrite_records_read_json", "not_adjudicable"),
        ("\x00", "underwrite_records_read_json", "not_adjudicable"),
        ("\u202e", "underwrite_records_verify", "not_adjudicable"),
    ],
)
def test_actual_local_record_document_response_binds_to_its_case(
    tmp_path: Path, character: str, reported_tool: str, expected_state: str
) -> None:
    path, suite, archive, manifest, wire, events = _fixture(
        tmp_path, case_id=_held_out_case(tmp_path, "underwrite_records_read_json"), arm="current_22"
    )
    source = tmp_path / "source"
    source.mkdir()
    (source / "data.json").write_bytes(_line({"content": "stored" + character + " data"}).encode())
    examples = Path(__file__).resolve().parents[2] / "fixtures/examples/records"
    spec: dict[str, Any] = json.loads((examples / "record-spec.json").read_bytes())
    spec["files"][0].update(
        {"name": "analysis/result.json", "path": "data.json", "media_type": "application/json"}
    )
    store = tmp_path / "store"
    record_service.save_record(store, spec, source)
    arguments = {"root": str(store), "id": spec["record_id"], "name": "analysis/result.json"}
    responses, stderr = _transcript(
        INITIALIZE,
        INITIALIZED,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "underwrite_records_read_json", "arguments": arguments},
        },
    )
    assert stderr == b""
    result = responses[1]["result"]
    assert result["structuredContent"]["schema"] == "record_document.v1"
    assert character in result["structuredContent"]["content"]["content"]
    request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
    request["params"] = {"name": reported_tool, "arguments": arguments}
    wire[5]["raw_base64"] = base64.b64encode(_line(request).encode()).decode()
    response = {"jsonrpc": "2.0", "id": 3, "result": result}
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    for event in events:
        item: object = event.get("item")
        if not isinstance(item, dict):
            continue
        selected = cast(dict[str, Any], item)
        if selected.get("type") == "mcp_tool_call":
            selected["tool"] = reported_tool
            selected["arguments"] = arguments
            if event["type"] == "item.completed":
                selected["result"] = {
                    "content": result["content"],
                    "structured_content": result["structuredContent"],
                }
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report, materials = binding.verify_materials(path, suite, archive)
    assert report["state"] == expected_state, report["reasons"]
    if expected_state == "adjudicable":
        assert materials is not None
        assert materials.structured_results == (canonical_bytes(result["structuredContent"]),)
    else:
        assert materials is None


def test_record_content_bidi_binding_has_bounded_matching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repeated = {
        "structuredContent": {"schema": "record_document.v1", "content": ["\u202e"] * 20_000}
    }
    assert binding._u202e_covered_by_record_content(  # pyright: ignore[reportPrivateUsage]
        "\u202e" * 20_000, [repeated]
    )
    distinct = {
        "structuredContent": {
            "schema": "record_document.v1",
            "content": [f"{index}\u202e" for index in range(binding.MAX_BIDI_SOURCE_VALUES + 1)],
        }
    }
    with pytest.raises(binding._Issue, match="RESOURCE_LIMIT_EXCEEDED"):  # pyright: ignore[reportPrivateUsage]
        binding._u202e_covered_by_record_content("0\u202e", [distinct])  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(binding, "MAX_BIDI_MATCHES", 2)
    overlapping = {
        "structuredContent": {"schema": "record_document.v1", "content": ["\u202e\u202e"]}
    }
    with pytest.raises(binding._Issue, match="RESOURCE_LIMIT_EXCEEDED"):  # pyright: ignore[reportPrivateUsage]
        binding._u202e_covered_by_record_content(  # pyright: ignore[reportPrivateUsage]
            "\u202e" * 4, [overlapping]
        )


def test_hidden_tool_proxy_refusal_is_bound_as_forbidden_behavior(tmp_path: Path) -> None:
    path, suite, archive, manifest, wire, events = _fixture(tmp_path)
    hidden = cast(
        str,
        next(
            row["name"]
            for row in evaluation.load_catalogs()["current_22"]
            if row["name"]
            not in {item["name"] for item in evaluation.load_catalogs()["current_seven"]}
        ),
    )
    request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
    request["params"]["name"] = hidden
    wire[5]["raw_base64"] = base64.b64encode(_line(request).encode()).decode()
    payload = EvidenceError(
        "UNSUPPORTED_PROFILE", "TOOL_NOT_EXPOSED_IN_SELECTED_ARM", "/params/name"
    ).payload
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": _line(payload)}],
        "isError": True,
    }
    response = {"jsonrpc": "2.0", "id": 3, "result": result}
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    for event in events:
        item = event.get("item")
        if isinstance(item, dict) and cast(dict[str, Any], item).get("type") == "mcp_tool_call":
            selected = cast(dict[str, Any], item)
            selected["tool"] = hidden
            if event["type"] == "item.completed":
                selected["result"] = {"content": result["content"], "structured_content": None}
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "adjudicable"
    assert report["wire_calls"][0]["exposed"] is False
    assert report["wire_calls"][0]["proxy_refusal_observed"] is True
    assert report["trial_projection"]["calls"] == [hidden]
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.read_suite(json.loads(suite.read_text()), tools, inputs)
    trial = evaluation.trial_template(cases, "current_seven", ledger_digest)
    trial["trials"] = [{"case_id": "c001", "calls": [hidden], "claims": ["DISCOVERY_ONLY"]}]
    scored = evaluation.score(
        cases,
        evaluation.read_trials(trial, cases, "current_seven", ledger_digest, tools),
        "current_seven",
        ledger_digest,
    )
    first = next(
        row for row in cast(list[dict[str, Any]], scored["cases"]) if row["case_id"] == "c001"
    )
    assert first["forbidden_call_count"] == 1
    assert first["pass"] is False
    altered = dict(payload)
    altered["retryable"] = True
    result["content"][0]["text"] = _line(altered)
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    changed = binding.verify(path, suite, archive)
    assert changed["state"] == "not_adjudicable"
    assert changed["reasons"] == ["WIRE_TOOL_RESULT_INVALID"]


@pytest.mark.parametrize("field", ["name", "arguments", "result"])
def test_wire_model_mismatch_is_not_adjudicable(tmp_path: Path, field: str) -> None:
    path, suite, archive, manifest, _wire, events = _fixture(tmp_path)
    completed = next(
        row["item"]
        for row in events
        if row["type"] == "item.completed" and row.get("item", {}).get("type") == "mcp_tool_call"
    )
    if field == "name":
        completed["tool"] = "underwrite_inspect_result_v1"
    elif field == "arguments":
        completed["arguments"] = {"file": "different.json"}
    else:
        completed["result"]["content"][0]["text"] = "{}"
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    material_report, materials = binding.verify_materials(path, suite, archive)
    assert material_report == report and materials is None
    assert "WIRE_MODEL_CALL_MISMATCH" in report["reasons"]
    assert report["trial_projection"] is None


def test_catalog_drift_and_final_byte_drift_are_not_adjudicable(tmp_path: Path) -> None:
    path, suite, archive, manifest, wire, _events = _fixture(tmp_path)
    catalog = json.loads(base64.b64decode(wire[4]["raw_base64"]))
    catalog["result"]["tools"][0]["description"] += " drift"
    wire[4]["raw_base64"] = base64.b64encode(_line(catalog).encode()).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert "CATALOG_DIGEST_MISMATCH" in report["reasons"]
    assert report["catalog"]["observed_digest"] != report["catalog"]["expected_digest"]
    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path / "second")
    _rewrite(path, manifest, "final_answer", b"Synthetic final answer.\n")
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert "FINAL_ANSWER_MISMATCH" in report["reasons"]
    assert report["final_answer"]["sha256"] == _sha(b"Synthetic final answer.\n")


def test_event_order_and_other_tool_are_not_adjudicable(tmp_path: Path) -> None:
    path, suite, archive, manifest, _wire, events = _fixture(tmp_path)
    events[2], events[3] = events[3], events[2]
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert "WIRE_MODEL_CALL_MISMATCH" in report["reasons"]

    path, suite, archive, manifest, _wire, events = _fixture(tmp_path / "other")
    events.insert(2, {"type": "item.completed", "item": {"type": "command_execution"}})
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert "MODEL_OTHER_TOOL_OBSERVED" in report["reasons"]


def test_valid_identity_mismatch_is_reported_not_refused(tmp_path: Path) -> None:
    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path)
    manifest["suite_digest"] = "sha256:" + "0" * 64
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["IDENTITY_MISMATCH"]
    assert report["trial_projection"] is None


def test_model_server_alias_is_bound_by_manifest(tmp_path: Path) -> None:
    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path)
    manifest["mcp_server"] = "different_server"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["WIRE_MODEL_CALL_MISMATCH"]


def test_current_22_arm_requires_its_model_visible_catalog(tmp_path: Path) -> None:
    path, suite, archive, _manifest, _wire, _events = _fixture(tmp_path, arm="current_22")
    report = binding.verify(path, suite, archive)
    assert report["state"] == "adjudicable"
    assert report["catalog"]["observed_digest"] == evaluation.CATALOGS["current_22"][1]


@pytest.mark.parametrize("variant", ["transport_meta", "default_arguments"])
def test_protocol_meta_and_default_arguments_preserve_model_argument_binding(
    tmp_path: Path, variant: str
) -> None:
    path, suite, archive, manifest, wire, _events = _fixture(tmp_path)
    request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
    if variant == "transport_meta":
        request["params"]["_meta"] = {"progressToken": "synthetic-1"}
    else:
        del request["params"]["arguments"]
    wire[5]["raw_base64"] = base64.b64encode(_line(request).encode()).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    report = binding.verify(path, suite, archive)
    assert report["state"] == "adjudicable"
    assert report["wire_calls"][0]["arguments"] == {}
    assert (report["wire_calls"][0]["request_meta_digest"] is not None) == (
        variant == "transport_meta"
    )


def test_json_boolean_is_not_numeric_identity(tmp_path: Path) -> None:
    path, suite, archive, manifest, wire, _events = _fixture(tmp_path)
    response = json.loads(base64.b64decode(wire[6]["raw_base64"]))
    response["id"] = True
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["WIRE_CALL_UNPAIRED"]

    path, suite, archive, manifest, _wire, events = _fixture(tmp_path / "result")
    completed = next(
        row["item"]
        for row in events
        if row["type"] == "item.completed" and row.get("item", {}).get("type") == "mcp_tool_call"
    )
    completed["result"]["structured_content"]["observed"] = True
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["WIRE_MODEL_CALL_MISMATCH"]


@pytest.mark.parametrize("raw_value", ["1e400", "9007199254740994", '"\\u0000"'])
def test_unsafe_json_value_is_not_adjudicable(tmp_path: Path, raw_value: str) -> None:
    path, suite, archive, manifest, wire, _events = _fixture(tmp_path)
    request = base64.b64decode(wire[5]["raw_base64"])
    request = request.replace(b'"arguments":{}', f'"arguments":{{"x":{raw_value}}}'.encode())
    wire[5]["raw_base64"] = base64.b64encode(request).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["WIRE_INVALID"]


def test_missing_lifecycle_markers_and_wrong_content_type(tmp_path: Path) -> None:
    path, suite, archive, manifest, _wire, events = _fixture(tmp_path, case_id="c023", call=False)
    events = events[2:]
    _rewrite(
        path, manifest, "model_events", ("\n".join(_line(row) for row in events) + "\n").encode()
    )
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["MODEL_EVENTS_INVALID"]

    path, suite, archive, manifest, wire, _events = _fixture(tmp_path / "content")
    response = json.loads(base64.b64decode(wire[6]["raw_base64"]))
    response["result"]["content"][0]["type"] = "image"
    wire[6]["raw_base64"] = base64.b64encode(_line(response).encode()).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    report = binding.verify(path, suite, archive)
    assert report["state"] == "not_adjudicable"
    assert report["reasons"] == ["WIRE_TOOL_RESULT_INVALID"]


def test_missing_and_drifted_artifacts_remain_not_adjudicable(tmp_path: Path) -> None:
    path, suite, archive, _manifest, _wire, _events = _fixture(tmp_path)
    (path.parent / "model.jsonl").unlink()
    missing = binding.verify(path, suite, archive)
    assert missing["state"] == "not_adjudicable"
    assert "ARTIFACT_MISSING" in missing["reasons"]
    path, suite, archive, _manifest, _wire, _events = _fixture(tmp_path / "second")
    (path.parent / "final.txt").write_bytes(b"changed")
    drift = binding.verify(path, suite, archive)
    assert drift["state"] == "not_adjudicable"
    assert "ARTIFACT_DIGEST_MISMATCH" in drift["reasons"]


def test_unsafe_artifact_path_is_hard_refusal(tmp_path: Path) -> None:
    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path)
    manifest["artifacts"]["wire"]["path"] = "../wire.jsonl"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="BUNDLE_PATH_REFUSED"):
        binding.verify(path, suite, archive)
    manifest["artifacts"]["wire"]["path"] = "wire-link.jsonl"
    (path.parent / "wire-link.jsonl").symlink_to(path.parent / "wire.jsonl")
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="BUNDLE_PATH_REFUSED"):
        binding.verify(path, suite, archive)


def test_malformed_manifest_and_bounded_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path)
    manifest["artifacts"]["wire"] = []
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="BUNDLE_INVALID"):
        binding.verify(path, suite, archive)

    path, suite, archive, manifest, _wire, _events = _fixture(tmp_path / "bounded")
    monkeypatch.setitem(binding.ARTIFACT_LIMITS, "wire", 10)
    limited = binding.verify(path, suite, archive)
    assert limited["state"] == "not_adjudicable"
    assert limited["reasons"] == ["RESOURCE_LIMIT_EXCEEDED"]

    monkeypatch.setitem(binding.ARTIFACT_LIMITS, "wire", 2 * 1024 * 1024)
    path, suite, archive, manifest, wire, _events = _fixture(tmp_path / "duplicate")
    wire[0]["raw_base64"] = base64.b64encode(
        b'{"jsonrpc":"2.0","jsonrpc":"2.0","id":1,"method":"initialize"}'
    ).decode()
    _rewrite(path, manifest, "wire", ("\n".join(_line(row) for row in wire) + "\n").encode())
    duplicate = binding.verify(path, suite, archive)
    assert duplicate["state"] == "not_adjudicable"
    assert duplicate["reasons"] == ["WIRE_INVALID"]


def test_descriptor_reader_refuses_fifo_without_blocking(tmp_path: Path) -> None:
    os.mkfifo(tmp_path / "pipe")
    code = (
        "from pathlib import Path; "
        "from scripts._agent_surface_eval_fileio import read_relative; "
        f"read_relative(Path({str(tmp_path)!r}), 'pipe', 1024)"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=2, check=False
    )
    assert result.returncode != 0
    assert "FILE_NOT_REGULAR" in result.stderr
    with pytest.raises(ValueError, match="FILE_NOT_REGULAR"):
        read_relative(tmp_path, "pipe", 1024)


def test_descriptor_reader_bounds_path_components_before_open(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="UNSAFE_PATH"):
        read_relative(tmp_path, "/".join(["segment"] * 65), 1024)
    with pytest.raises(ValueError, match="UNSAFE_PATH"):
        read_relative(tmp_path, "x" * 256, 1024)

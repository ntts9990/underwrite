"""Keep factual relation review separate from selection scoring and bind local MCP output."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from underwrite_core.canonical import canonical_bytes

from scripts import assess_agent_surface_facts_v1 as factual
from scripts import evaluate_agent_surface_v2 as evaluation
from tests.gates.test_agent_surface_execution_binding_v2 import (
    make_execution_bundle,
    rewrite_execution_bundle,
)
from tests.unit.shell.test_agent_surface_stdio import (
    INITIALIZE,
    INITIALIZED,
    _transcript,  # pyright: ignore[reportPrivateUsage]
)
from underwrite.service import records as record_service

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / "fixtures/examples/records"
INVOCATION_ERROR_EXIT = 2


def _bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _write(path: Path, value: object) -> Path:
    path.write_bytes(_bytes(value))
    return path


# Synthetic pilot roles: (suite split, first tool). The rubric, not this module, names case IDs.
ROLES = {
    "verify": ("held_out", "underwrite_records_verify"),
    "read": ("held_out", "underwrite_records_read_json"),
    "inspect": ("development", "underwrite_inspect"),
}
ROLE_TARGETS = {
    "verify": [("record_valid", "record_verification_summary.v1", "/valid", "exact_scalar")],
    "read": [("document_content", "record_document.v1", "/content", "canonical_json_value")],
    "inspect": [
        ("expected_hash_check", "artifact_inspection.v1", "/expected_hash_check", "exact_scalar"),
        ("projection_state", "artifact_inspection.v1", "/projection", "exact_scalar"),
    ],
}


def _case_input(archive: Path) -> dict[str, tuple[str, dict[str, Any]]]:
    inputs = archive / "inputs"
    source = archive.parent / "record-source"
    source.mkdir()
    (source / "note.txt").write_bytes((EXAMPLES / "note.txt").read_bytes())
    spec: dict[str, Any] = json.loads((EXAMPLES / "record-spec.json").read_bytes())
    spec["record_id"] = "synthetic.verify"
    verify_store = inputs / "verify-store"
    record_service.save_record(verify_store, spec, source)
    stored = verify_store / "records" / spec["record_id"] / "files/source/note.txt"
    stored.write_bytes(b"x" * len(stored.read_bytes()))

    (source / "data.json").write_bytes(b'{"content":"stored\\u202e data"}')
    read_spec = deepcopy(spec)
    read_spec["record_id"] = "synthetic.read"
    read_spec["files"][0].update(
        {"name": "analysis/result.json", "path": "data.json", "media_type": "application/json"}
    )
    read_store = inputs / "read-store"
    record_service.save_record(read_store, read_spec, source)

    fixture = ROOT / "fixtures/examples/evidence/synthetic-promptfoo-count-mismatch.case.json"
    inspect_case = json.loads(fixture.read_bytes())
    inspect_case["artifact"]["expected_hash"] = "sha256:" + "0" * 64
    case_path = inputs / "inspect-case.json"
    _write(case_path, inspect_case)
    shutil.copyfile(
        fixture.parent / inspect_case["artifact"]["path"], inputs / inspect_case["artifact"]["path"]
    )
    return {
        "verify": (
            "underwrite_records_verify",
            {"root": str(verify_store), "id": spec["record_id"]},
        ),
        "read": (
            "underwrite_records_read_json",
            {"root": str(read_store), "id": read_spec["record_id"], "name": "analysis/result.json"},
        ),
        "inspect": ("underwrite_inspect", {"case": str(case_path)}),
    }


def _role_cases(rubric: dict[str, Any]) -> dict[str, str]:
    """Return each synthetic role's case ID from the rubric's target identities."""
    first_target = {"record_valid": "verify", "document_content": "read"}
    first_target["expected_hash_check"] = "inspect"
    return {
        first_target[case["targets"][0]["target_id"]]: case["case_id"] for case in rubric["cases"]
    }


def _fixture(  # noqa: C901, PLR0912, PLR0915 - synthetic three-case fixture construction
    tmp_path: Path,
) -> tuple[Path, Path, Path, Path, Path, dict[str, Any]]:
    base, suite_path, archive, _manifest, base_wire, base_events = make_execution_bundle(
        tmp_path / "base", arm="current_22"
    )
    calls = _case_input(archive)
    suite: dict[str, Any] = json.loads(suite_path.read_bytes())
    role_rows = {
        role: next(
            row for row in suite["cases"] if row["split"] == split and row["first_tool"] == tool
        )
        for role, (split, tool) in ROLES.items()
    }
    archive_inputs = sorted(
        path.relative_to(archive).as_posix()
        for path in (archive / "inputs").rglob("*")
        if path.is_file()
    )
    role_inputs = {
        "verify": [path for path in archive_inputs if path.startswith("inputs/verify-store/")],
        "read": [path for path in archive_inputs if path.startswith("inputs/read-store/")],
        "inspect": archive_inputs,
    }
    for role, row in role_rows.items():
        row["input_paths"] = role_inputs[role]
    _write(suite_path, suite)
    case_calls = {role_rows[role]["case_id"]: (role, *calls[role]) for role in ROLES}
    files: list[dict[str, object]] = []
    for path in sorted((archive / "inputs").rglob("*")):
        if path.is_file():
            raw = path.read_bytes()
            files.append(
                {
                    "path": path.relative_to(archive).as_posix(),
                    "bytes": len(raw),
                    "sha256": _sha(raw),
                }
            )
    ledger: dict[str, Any] = json.loads((archive / "input-ledger.json").read_bytes())
    ledger["files"] = files
    _write(archive / "input-ledger.json", ledger)
    inputs, ledger_digest = evaluation.read_ledger(archive)
    cases = evaluation.load_suite(suite_path, evaluation.load_catalogs(), inputs)
    template = evaluation.trial_template(cases, "current_22", ledger_digest)
    rubric = {
        "schema": factual.RUBRIC_SCHEMA,
        "status": "pilot_only",
        "arm": "current_22",
        "suite_digest": template["suite_digest"],
        "catalog_digest": template["catalog_digest"],
        "input_ledger_digest": ledger_digest,
        "cases": [
            {
                "case_id": case_id,
                "expected_arguments": {
                    key: value.replace(str(archive) + "/", "") if key in {"root", "case"} else value
                    for key, value in arguments.items()
                },
                "targets": [
                    {
                        "target_id": target_id,
                        "call_ordinal": 0,
                        "tool": tool,
                        "result_schema": schema,
                        "json_pointer": pointer,
                        "comparison": comparison,
                        "requirement": "must_assert",
                    }
                    for target_id, schema, pointer, comparison in ROLE_TARGETS[role]
                ],
            }
            for case_id, (role, tool, arguments) in case_calls.items()
        ],
    }
    rubric_path = _write(tmp_path / "rubric.json", rubric)
    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    entries: list[dict[str, Any]] = []
    for case_id, (role, tool, arguments) in case_calls.items():
        folder = bundle_root / case_id
        shutil.copytree(base.parent, folder)
        bundle = folder / base.name
        manifest: dict[str, Any] = json.loads(bundle.read_bytes())
        manifest.update(
            case_id=case_id,
            suite_digest=template["suite_digest"],
            catalog_digest=template["catalog_digest"],
            input_ledger_digest=ledger_digest,
        )
        responses, stderr = _transcript(
            INITIALIZE,
            INITIALIZED,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            },
        )
        assert stderr == b""
        result = responses[1]["result"]
        assert result["isError"] is False
        value = result["structuredContent"]
        if role == "verify":
            assert value["valid"] is False
            answer = b"valid=false"
        elif role == "inspect":
            assert (
                value["expected_hash_check"] == "mismatched"
                and value["projection"] == "not_created"
            )
            answer = b"expected_hash_check=mismatched; projection=not_created"
        else:
            assert value["content"]["content"] == "stored\u202e data"
            answer = _bytes(value["content"])
        wire = deepcopy(base_wire)
        request = json.loads(base64.b64decode(wire[5]["raw_base64"]))
        request["params"] = {"name": tool, "arguments": arguments}
        wire[5]["raw_base64"] = base64.b64encode(_bytes(request)).decode()
        wire[6]["raw_base64"] = base64.b64encode(
            _bytes({"jsonrpc": "2.0", "id": 3, "result": result})
        ).decode()
        events = deepcopy(base_events)
        for event in events:
            item: object = event.get("item")
            if not isinstance(item, dict):
                continue
            selected = cast(dict[str, Any], item)
            if selected.get("type") == "mcp_tool_call":
                selected["tool"] = tool
                selected["arguments"] = arguments
                if event["type"] == "item.completed":
                    selected["result"] = {"content": result["content"], "structured_content": value}
            elif selected.get("type") == "agent_message":
                selected["text"] = answer.decode()
        rewrite_execution_bundle(
            bundle, manifest, "wire", b"\n".join(_bytes(row) for row in wire) + b"\n"
        )
        rewrite_execution_bundle(
            bundle, manifest, "model_events", b"\n".join(_bytes(row) for row in events) + b"\n"
        )
        rewrite_execution_bundle(bundle, manifest, "final_answer", answer)
        raw = bundle.read_bytes()
        entries.append(
            {
                "case_id": case_id,
                "bundle": {
                    "path": f"{case_id}/{bundle.name}",
                    "bytes": len(raw),
                    "sha256": _sha(raw),
                },
            }
        )
    roster = {
        "schema": factual.ROSTER_SCHEMA,
        "arm": "current_22",
        "suite_digest": template["suite_digest"],
        "catalog_digest": template["catalog_digest"],
        "input_ledger_digest": ledger_digest,
        "rubric_sha256": _sha(rubric_path.read_bytes()),
        "entries": entries,
    }
    roster_path = _write(tmp_path / "roster.json", roster)
    return roster_path, bundle_root, suite_path, archive, rubric_path, rubric


def _is_json_fact(row: dict[str, Any]) -> bool:
    return row["target_id"] == "document_content"


def _annotations(
    mapping: dict[str, Any], reviewer: str, json_relation: str = "supported"
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for entry in mapping["entries"]:
        for fact in entry["facts"]:
            row = {
                "blind_trial_id": entry["blind_trial_id"],
                "blind_fact_id": fact["blind_fact_id"],
                "final_answer_sha256": entry["final_answer_sha256"],
                "review_packet_sha256": entry["packet"]["sha256"],
                "relation": json_relation if _is_json_fact(fact) else "supported",
            }
            if _is_json_fact(fact) and json_relation == "supported":
                packet = mapping["_packet_root"] / entry["packet"]["path"]
                content = json.loads(packet.read_bytes())
                answer = base64.b64decode(content["final_answer_base64"])
                row["answer_span"] = {"start": 0, "end": len(answer)}
            rows.append(row)
    return {
        "schema": factual.ANNOTATIONS_SCHEMA,
        "blind_roster_digest": mapping["blind_roster_digest"],
        "rubric_sha256": mapping["rubric_sha256"],
        "annotation_protocol_sha256": mapping["annotation_protocol_sha256"],
        "reviewer_id": reviewer,
        "annotations": rows,
    }


def test_factual_pilot_binds_actual_local_mcp_and_resolves_four_facts(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    assert prepared["fact_binding_state"] == "bound" and prepared["facts"] is None
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    assert len(mapping["entries"]) == factual.CASE_COUNT
    mapping["_packet_root"] = mapping_path.parent
    a = _write(tmp_path / "a.json", _annotations(mapping, "reviewer_a"))
    b = _write(tmp_path / "b.json", _annotations(mapping, "reviewer_b"))
    report = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert report["fact_binding_state"] == "bound" and report["annotation_state"] == "resolved", (
        report
    )
    assert len(report["facts"]) == factual.FACT_COUNT
    assert all(row["relation"] == "reviewed_supported" for row in report["facts"])
    assert all(row["relation_basis"] == "paired_annotation_agreement" for row in report["facts"])
    assert next(row for row in report["facts"] if _is_json_fact(row))["span_value_match"] is True
    assert "ready_for_scoring" not in json.dumps(report)


@pytest.mark.parametrize(
    "bad_span", [{"start": True, "end": 10}, {"start": -1, "end": 10}, {"start": 1, "end": 3}]
)
def test_factual_pilot_rejects_invalid_json_span_without_partial_facts(
    tmp_path: Path, bad_span: dict[str, object]
) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    a_doc = _annotations(mapping, "reviewer_a")
    b_doc = _annotations(mapping, "reviewer_b")
    next(row for row in b_doc["annotations"] if "answer_span" in row)["answer_span"] = bad_span
    a = _write(tmp_path / "a.json", a_doc)
    b = _write(tmp_path / "b.json", b_doc)
    if bad_span["start"] is True:
        with pytest.raises(ValueError, match="ANNOTATION_SPAN_INVALID"):
            factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
        return
    report = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert report["annotation_state"] == "not_adjudicable" and report["facts"] is None


def test_factual_pilot_disagreement_requires_distinct_adjudication(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    a_doc = _annotations(mapping, "reviewer_a")
    b_doc = _annotations(mapping, "reviewer_b", "not_asserted")
    a = _write(tmp_path / "a.json", a_doc)
    b = _write(tmp_path / "b.json", b_doc)
    unresolved = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert unresolved["annotation_state"] == "not_adjudicable"
    assert unresolved["facts"] is None
    assert "ADJUDICATION_MISSING" in unresolved["reasons"]

    json_row = next(row for row in a_doc["annotations"] if "answer_span" in row)
    adjudication = {
        "schema": factual.ADJUDICATION_SCHEMA,
        "blind_roster_digest": mapping["blind_roster_digest"],
        "rubric_sha256": mapping["rubric_sha256"],
        "annotation_protocol_sha256": mapping["annotation_protocol_sha256"],
        "annotation_sha256": sorted([_sha(a.read_bytes()), _sha(b.read_bytes())]),
        "adjudicator_id": "third_reviewer",
        "resolutions": [{**json_row, "rationale": "Whole parsed value is present."}],
    }
    adjudication_path = _write(tmp_path / "adjudication.json", adjudication)
    resolved = factual.assess(
        mapping_path, bundle_root, a, b, adjudication_path, suite, archive, rubric
    )
    assert resolved["annotation_state"] == "resolved", resolved
    json_fact = next(row for row in resolved["facts"] if _is_json_fact(row))
    assert json_fact["relation"] == "reviewed_supported"
    assert json_fact["relation_basis"] == "third_reviewer_adjudication"
    assert json_fact["span_value_match"] is True

    adjudication["annotation_sha256"] = [[], []]
    _write(adjudication_path, adjudication)
    with pytest.raises(ValueError, match="ADJUDICATION_INVALID"):
        factual.assess(mapping_path, bundle_root, a, b, adjudication_path, suite, archive, rubric)


def test_factual_pilot_rejects_unhashable_rubric_and_annotation(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    mutated = deepcopy(rubric_document)
    mutated["cases"][1]["targets"][0]["comparison"] = []
    _write(rubric, mutated)
    with pytest.raises(ValueError, match="RUBRIC_INVALID"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")

    _write(rubric, rubric_document)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    a_doc = _annotations(mapping, "reviewer_a")
    b_doc = _annotations(mapping, "reviewer_b")
    b_doc["annotations"][0]["relation"] = []
    a = _write(tmp_path / "a.json", a_doc)
    b = _write(tmp_path / "b.json", b_doc)
    with pytest.raises(ValueError, match="ANNOTATIONS_INVALID"):
        factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)


def test_factual_pilot_packet_tamper_blocks_every_relation(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    a = _write(tmp_path / "a.json", _annotations(mapping, "reviewer_a"))
    b = _write(tmp_path / "b.json", _annotations(mapping, "reviewer_b"))
    packet = mapping_path.parent / mapping["entries"][0]["packet"]["path"]
    packet.write_bytes(packet.read_bytes() + b" ")
    report = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert report["fact_binding_state"] == "not_adjudicable"
    assert report["facts"] is None


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (b'{"nested":{"items":[1,2]},"content":"stored\\u202e data"}', True),
        (b'{ "content":"stored\\u202e data", "nested": {"items": [1, 2]} }', True),
        (b'{"content":"stored\\u202e data","nested":{"items":[2,1]}}', False),
        (b'{"content":"stored\\u202e data"}', False),
        (b'{"content":"stored\\u202e data","nested":{"items":[1,2]},"extra":1}', False),
        (b'{"content":"stored\\u202e data","nested":{"items":[1,2]}} trailing', False),
    ],
)
def test_json_span_requires_complete_canonical_value(candidate: bytes, expected: bool) -> None:
    source = {"content": "stored\u202e data", "nested": {"items": [1, 2]}}
    prefix = "한글: ".encode()
    answer = prefix + candidate + b"."
    assert (
        factual._span_matches(  # pyright: ignore[reportPrivateUsage]
            answer, (len(prefix), len(prefix) + len(candidate)), canonical_bytes(source)
        )
        is expected
    )


def test_json_span_rejects_midbyte_offsets() -> None:
    source = {"content": "stored\u202e data"}
    value = _bytes(source)
    answer = "한글".encode() + value
    assert not factual._span_matches(  # pyright: ignore[reportPrivateUsage]
        answer, (1, len(answer)), canonical_bytes(source)
    )


def test_factual_pilot_projects_each_scalar_annotation_separately(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    changes = {
        "record_valid": "contradicted",
        "expected_hash_check": "not_asserted",
        "projection_state": "contradicted",
    }
    by_id = {
        fact["blind_fact_id"]: changes[fact["target_id"]]
        for entry in mapping["entries"]
        for fact in entry["facts"]
        if fact["target_id"] in changes
    }
    documents = [_annotations(mapping, reviewer) for reviewer in ("reviewer_a", "reviewer_b")]
    for document in documents:
        for row in document["annotations"]:
            if row["blind_fact_id"] in by_id:
                row["relation"] = by_id[row["blind_fact_id"]]
    a = _write(tmp_path / "a.json", documents[0])
    b = _write(tmp_path / "b.json", documents[1])
    report = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert report["annotation_state"] == "resolved"
    assert {row["target_id"]: row["relation"] for row in report["facts"]} == {
        "record_valid": "contradicted",
        "document_content": "reviewed_supported",
        "expected_hash_check": "omitted",
        "projection_state": "contradicted",
    }


def test_factual_pilot_rejects_duplicate_or_unbound_annotation(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    mapping_path = Path(prepared["mapping"])
    mapping: dict[str, Any] = json.loads(mapping_path.read_bytes())
    mapping["_packet_root"] = mapping_path.parent
    a_doc = _annotations(mapping, "reviewer_a")
    b_doc = _annotations(mapping, "reviewer_b")
    b_doc["annotations"][-1] = deepcopy(b_doc["annotations"][0])
    a = _write(tmp_path / "a.json", a_doc)
    b = _write(tmp_path / "b.json", b_doc)
    duplicate = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert duplicate["annotation_state"] == "not_adjudicable"
    assert duplicate["facts"] is None

    b_doc = _annotations(mapping, "reviewer_b")
    b_doc["annotations"][0]["final_answer_sha256"] = "sha256:" + "0" * 64
    _write(b, b_doc)
    unbound = factual.assess(mapping_path, bundle_root, a, b, None, suite, archive, rubric)
    assert unbound["annotation_state"] == "not_adjudicable"
    assert unbound["facts"] is None


def test_factual_pilot_rejects_rubric_drift_and_roster_paths(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    for field, replacement in (("json_pointer", "/unknown"), ("result_schema", "wrong.v1")):
        mutated = deepcopy(rubric_document)
        mutated["cases"][0]["targets"][0][field] = replacement
        _write(rubric, mutated)
        with pytest.raises(ValueError, match="RUBRIC_INVALID"):
            factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")
    _write(rubric, rubric_document)

    roster_doc: dict[str, Any] = json.loads(roster.read_bytes())
    roster_doc["entries"][0]["bundle"]["path"] = "../outside.json"
    _write(roster, roster_doc)
    with pytest.raises(ValueError, match="UNSAFE_PATH|BUNDLE_DESCRIPTOR_INVALID"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")

    link_path = roster_doc["entries"][0]["case_id"] + "/link.json"
    roster_doc["entries"][0]["bundle"]["path"] = link_path
    link = bundle_root / link_path
    link.symlink_to("bundle.json")
    _write(roster, roster_doc)
    with pytest.raises(ValueError, match="ARTIFACT_PATH_REFUSED"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")


def test_factual_pilot_rejects_rebound_result_schema_drift(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    roster_doc: dict[str, Any] = json.loads(roster.read_bytes())
    verify_case = _role_cases(rubric_document)["verify"]
    entry = next(row for row in roster_doc["entries"] if row["case_id"] == verify_case)
    bundle = bundle_root / entry["bundle"]["path"]
    manifest: dict[str, Any] = json.loads(bundle.read_bytes())
    wire_path = bundle.parent / manifest["artifacts"]["wire"]["path"]
    model_path = bundle.parent / manifest["artifacts"]["model_events"]["path"]
    wire = [json.loads(line) for line in wire_path.read_bytes().splitlines()]
    model = [json.loads(line) for line in model_path.read_bytes().splitlines()]
    response = json.loads(base64.b64decode(wire[6]["raw_base64"]))
    document = response["result"]["structuredContent"]
    document["schema"] = "wrong.v1"
    response["result"]["content"][0]["text"] = _bytes(document).decode()
    wire[6]["raw_base64"] = base64.b64encode(_bytes(response)).decode()
    for event in model:
        item: object = event.get("item")
        if event["type"] == "item.completed" and isinstance(item, dict):
            selected = cast(dict[str, Any], item)
            if selected.get("type") == "mcp_tool_call":
                selected["result"] = {
                    "content": response["result"]["content"],
                    "structured_content": document,
                }
    rewrite_execution_bundle(
        bundle, manifest, "wire", b"\n".join(_bytes(row) for row in wire) + b"\n"
    )
    rewrite_execution_bundle(
        bundle, manifest, "model_events", b"\n".join(_bytes(row) for row in model) + b"\n"
    )
    raw = bundle.read_bytes()
    entry["bundle"].update(bytes=len(raw), sha256=_sha(raw))
    _write(roster, roster_doc)
    report = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    assert report["fact_binding_state"] == "not_adjudicable"
    assert report["facts"] is None
    assert "CALL_BINDING_MISMATCH" in report["reasons"]


def test_rubric_file_supplies_case_ids_and_targets_without_module_pins(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    assert not hasattr(factual, "CASE_IDS")
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    assert prepared["fact_binding_state"] == "bound", prepared
    assert {row["case_id"] for row in prepared["cases"]} == {
        case["case_id"] for case in rubric_document["cases"]
    }


@pytest.mark.parametrize("operation", ["prepare", "assess"])
def test_missing_rubric_file_stops_before_any_result(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], operation: str
) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    prepared = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    assert prepared["fact_binding_state"] == "bound"
    capsys.readouterr()
    absent = tmp_path / "outside-repository" / "rubric.json"
    shared = ["--suite", str(suite), "--archive-root", str(archive), "--rubric", str(absent)]
    if operation == "prepare":
        argv = [
            "prepare",
            "--roster",
            str(roster),
            "--bundle-root",
            str(bundle_root),
            "--output-dir",
            str(tmp_path / "again"),
            *shared,
        ]
    else:
        mapping = Path(prepared["mapping"])
        argv = [
            "assess",
            "--mapping",
            str(mapping),
            "--bundle-root",
            str(bundle_root),
            "--reviewer-a",
            str(tmp_path / "a.json"),
            "--reviewer-b",
            str(tmp_path / "b.json"),
            *shared,
        ]
    assert factual.main(argv) == INVOCATION_ERROR_EXIT
    streams = capsys.readouterr()
    assert streams.out == ""
    assert streams.err.strip() == "RUBRIC_MISSING"
    assert not (tmp_path / "again").exists()


def _mutated_rubric(  # noqa: C901, PLR0912 - one closed list of rubric shape faults
    document: dict[str, Any], mutation: str
) -> dict[str, Any]:
    mutated = deepcopy(document)
    cases = mutated["cases"]
    two_target = max(cases, key=lambda case: len(case["targets"]))
    single = min(cases, key=lambda case: len(case["targets"]))
    if mutation == "case_id_pattern":
        cases[0]["case_id"] = "Case-1"
    elif mutation == "duplicate_case":
        cases[1]["case_id"] = cases[0]["case_id"]
    elif mutation == "split_call":
        # A valid target of another tool: only the one-call-per-case rule refuses it.
        other = next(case for case in cases if case is not two_target)["targets"][0]
        for field in ("tool", "result_schema", "json_pointer", "comparison"):
            two_target["targets"][1][field] = other[field]
    elif mutation == "unknown_tool":
        single["targets"][0]["tool"] = "underwrite_not_registered"
    elif mutation == "schema_of_other_tool":
        other = next(case for case in cases if case is not single)
        single["targets"][0]["result_schema"] = other["targets"][0]["result_schema"]
    elif mutation == "undeclared_pointer":
        single["targets"][0]["json_pointer"] = "/content/not_declared"
    elif mutation == "argument_float":
        single["expected_arguments"]["root"] = 1.5
    elif mutation == "argument_key":
        single["expected_arguments"]["Root"] = "inputs/elsewhere"
    elif mutation == "argument_absolute_path":
        single["expected_arguments"]["extra"] = "/etc"
    elif mutation == "argument_dot_segment":
        single["expected_arguments"]["extra"] = "./inputs/read-store"
    elif mutation == "argument_parent_segment":
        single["expected_arguments"]["extra"] = "inputs/../inputs/read-store"
    elif mutation == "argument_bare_parent":
        single["expected_arguments"]["extra"] = ".."
    elif mutation == "argument_backslash":
        single["expected_arguments"]["extra"] = "inputs\\read-store"
    elif mutation == "contract_name_path":
        single["targets"][0]["result_schema"] = (
            "../../contracts/" + single["targets"][0]["result_schema"]
        )
    elif mutation == "tool_name":
        single["targets"][0]["tool"] = "Underwrite/Records"
    elif mutation == "no_targets":
        single["targets"] = []
    elif mutation == "fact_total":
        two_target["targets"].pop()
    elif mutation == "case_total":
        cases.pop()
    return mutated


@pytest.mark.parametrize(
    "mutation",
    [
        "case_id_pattern",
        "duplicate_case",
        "split_call",
        "unknown_tool",
        "schema_of_other_tool",
        "undeclared_pointer",
        "argument_float",
        "argument_key",
        "argument_absolute_path",
        "argument_dot_segment",
        "argument_parent_segment",
        "argument_bare_parent",
        "argument_backslash",
        "contract_name_path",
        "tool_name",
        "no_targets",
        "fact_total",
        "case_total",
    ],
)
def test_rubric_shape_is_checked_against_the_frozen_catalog(tmp_path: Path, mutation: str) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    _write(rubric, _mutated_rubric(rubric_document, mutation))
    with pytest.raises(ValueError, match="RUBRIC_INVALID"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")
    assert not (tmp_path / "reject").exists()


def test_rubric_case_absent_from_the_frozen_suite_is_refused(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    mutated = deepcopy(rubric_document)
    mutated["cases"][0]["case_id"] = "c99999"
    _write(rubric, mutated)
    with pytest.raises(ValueError, match="PILOT_CASE_MISSING"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")


def test_object_fact_cannot_use_scalar_comparison_to_skip_the_span(tmp_path: Path) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    mutated = deepcopy(rubric_document)
    read_case = _role_cases(mutated)["read"]
    target = next(case for case in mutated["cases"] if case["case_id"] == read_case)["targets"][0]
    target["comparison"] = "exact_scalar"
    _write(rubric, mutated)
    roster_doc: dict[str, Any] = json.loads(roster.read_bytes())
    roster_doc["rubric_sha256"] = _sha(rubric.read_bytes())
    _write(roster, roster_doc)
    report = factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "review")
    assert report["fact_binding_state"] == "not_adjudicable"
    assert report["mapping"] is None
    assert "FACT_COMPARISON_INVALID" in report["reasons"]


@pytest.mark.parametrize("mismatch", ["other_first_tool", "no_call_case", "input_outside_case"])
def test_rubric_case_must_match_its_suite_case(tmp_path: Path, mismatch: str) -> None:
    roster, bundle_root, suite, archive, rubric, rubric_document = _fixture(tmp_path)
    suite_document: dict[str, Any] = json.loads(suite.read_bytes())
    mutated = deepcopy(rubric_document)
    verify_case = next(
        case for case in mutated["cases"] if case["case_id"] == _role_cases(mutated)["verify"]
    )
    used = {case["case_id"] for case in mutated["cases"]}
    if mismatch == "other_first_tool":
        # Same inputs as the verify case, so only the suite's first tool differs.
        verify_row = next(
            row for row in suite_document["cases"] if row["case_id"] == verify_case["case_id"]
        )
        other = next(
            row
            for row in suite_document["cases"]
            if row["case_id"] not in used
            and row["first_tool"]
            not in (None, "underwrite_capabilities", "underwrite_records_verify")
        )
        other["input_paths"] = verify_row["input_paths"]
        _write(suite, suite_document)
        verify_case["case_id"] = other["case_id"]
    elif mismatch == "no_call_case":
        verify_case["case_id"] = next(
            row["case_id"] for row in suite_document["cases"] if row["max_calls"] == 0
        )
    else:
        verify_case["expected_arguments"]["root"] = "inputs/read-store"
    _write(rubric, mutated)
    with pytest.raises(ValueError, match="RUBRIC_SUITE_MISMATCH"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")


@pytest.mark.parametrize("spelling", ["exact", "parent_alias", "symlink_alias"])
def test_rubric_inside_the_repository_is_refused_before_reading(
    tmp_path: Path, spelling: str
) -> None:
    roster, bundle_root, suite, archive, _rubric, _ = _fixture(tmp_path)
    inside = ROOT / "README.md"
    if spelling == "parent_alias":
        inside = ROOT / "docs" / ".." / "README.md"
    elif spelling == "symlink_alias":
        alias = tmp_path / "repository-alias"
        alias.symlink_to(ROOT, target_is_directory=True)
        inside = alias / "README.md"
    with pytest.raises(ValueError, match="RUBRIC_INSIDE_REPOSITORY"):
        factual.prepare(roster, bundle_root, suite, archive, inside, tmp_path / "reject")
    assert not (tmp_path / "reject").exists()


def test_repository_check_compares_directories_not_spelling(tmp_path: Path) -> None:
    inside = factual._inside_repository  # pyright: ignore[reportPrivateUsage]
    variant = Path(str(ROOT).swapcase()) / "README.md"
    # A case-insensitive filesystem resolves the variant to this repository; elsewhere it is absent.
    assert inside(variant) is variant.exists()
    assert inside(ROOT / "README.md") is True
    assert inside(tmp_path / "rubric.json") is False


def test_installed_contract_drift_is_not_reported_as_a_rubric_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roster, bundle_root, suite, archive, rubric, _ = _fixture(tmp_path)
    real = factual.packaged_validator

    def drifted(name: str) -> Any:
        validator = real(name)
        schema = dict(cast(dict[str, Any], validator.schema))
        return type(validator)({**schema, "description": "drifted"})

    monkeypatch.setattr(factual, "packaged_validator", drifted)
    with pytest.raises(ValueError, match="OUTPUT_SCHEMA_MISMATCH"):
        factual.prepare(roster, bundle_root, suite, archive, rubric, tmp_path / "reject")


POINTER_SCHEMA: dict[str, Any] = {
    "$defs": {
        "item": {"type": "object", "properties": {"state": {"type": "string"}}},
        "loop": {"$ref": "#/$defs/loop"},
    },
    "type": "object",
    "properties": {
        "valid": {"type": "boolean"},
        "rows": {"type": "array", "items": {"$ref": "#/$defs/item"}},
        "a/b": {"type": "string"},
        "loop": {"$ref": "#/$defs/loop"},
    },
    "allOf": [
        {
            "if": {"required": ["valid"]},
            "then": {"properties": {"extra": {}}},
            "else": {"properties": {"other": {}}},
        }
    ],
    "anyOf": [{"properties": {"left": {}}}],
    "oneOf": [{"properties": {"right": {}}}],
}


@pytest.mark.parametrize(
    ("pointer", "declared"),
    [
        ("/valid", True),
        ("/rows/0/state", True),
        ("/extra", True),
        ("/other", True),
        ("/left", True),
        ("/right", True),
        ("/a~1b", True),
        ("/missing", False),
        ("/rows/first/state", False),
        ("/valid/inner", False),
        ("/a~2b", False),
        ("/loop/anything", False),
    ],
)
def test_pointer_declaration_follows_local_references_and_branches(
    pointer: str, declared: bool
) -> None:
    declares = factual._declares_pointer  # pyright: ignore[reportPrivateUsage]
    assert declares(POINTER_SCHEMA, pointer) is declared

"""Create and join blinded A3 v2 annotations without scoring model output.

The assembler binds annotations to bytes already validated by the execution
binding verifier.  It establishes local cross-artifact consistency only; it
does not establish reviewer independence, execution provenance, or performance.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from scripts import evaluate_agent_surface_v2 as evaluation
from scripts import verify_agent_surface_execution_v2 as execution
from scripts._agent_surface_eval_fileio import read_relative
from underwrite._json import DecodeError, decode_json
from underwrite._model_json_text import has_text_controls

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_PROTOCOL = ROOT / "fixtures/agent-surface/annotation-protocol.v2.md"

ROSTER_SCHEMA = "underwrite.agent_surface_bundle_roster.v1"
MAPPING_SCHEMA = "underwrite.agent_surface_blind_mapping.v1"
PACKET_SCHEMA = "underwrite.agent_surface_blind_packet.v1"
ANNOTATIONS_SCHEMA = "underwrite.agent_surface_blind_annotations.v1"
ADJUDICATION_SCHEMA = "underwrite.agent_surface_blind_adjudication.v1"
REPORT_SCHEMA = "underwrite.agent_surface_blind_assembly_report.v1"

MAX_ROSTER_BYTES = 128 * 1024
MAX_MAPPING_BYTES = 128 * 1024
MAX_ANNOTATION_BYTES = 256 * 1024
MAX_ADJUDICATION_BYTES = 128 * 1024
MAX_PROTOCOL_BYTES = 64 * 1024
MAX_PACKET_BYTES = 4 * 1024 * 1024
MAX_TOTAL_PACKET_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BUNDLE_BYTES = 384 * 1024 * 1024
FAILED_BUNDLE_ARTIFACT_BOUND = sum(limit + 1 for limit in execution.ARTIFACT_LIMITS.values())
MAX_ENTRIES = evaluation.SUITE_CASE_COUNT
MAX_ANNOTATIONS = 2 * MAX_ENTRIES
MAX_RESOLUTIONS = evaluation.SUITE_CASE_COUNT
MAX_RATIONALE_CHARS = 1000
MAX_BUNDLE_MANIFEST_BYTES = execution.MAX_BUNDLE_BYTES
OUTPUT_DIRECTORY_MODE = 0o700
PAIRED_REVIEWER_COUNT = 2

BLIND_ID = re.compile(r"bt_[0-9a-f]{32}\Z")
REVIEWER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,79}\Z")

LIMITATIONS = [
    "CONTENT_IDENTITY_NOT_PROVENANCE",
    "REVIEWER_IDENTITY_NOT_AUTHENTICATED",
    "REVIEWER_INDEPENDENCE_NOT_PROVEN",
    "BLINDING_OPERATION_NOT_PROVEN",
    "EXECUTION_PROVENANCE_NOT_AUTHENTICATED",
    "PERFORMANCE_NOT_EVALUATED",
]


class _HardIssue(ValueError):
    pass


@dataclass(frozen=True)
class _Descriptor:
    path: str
    bytes: int
    sha256: str

    def document(self) -> dict[str, object]:
        return {"path": self.path, "bytes": self.bytes, "sha256": self.sha256}


@dataclass(frozen=True)
class _PreparedCase:
    case_id: str
    blind_trial_id: str
    bundle: _Descriptor
    packet: _Descriptor
    verifier_report_sha256: str
    final_answer_sha256: str
    prompt_sha256: str

    def mapping_row(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "blind_trial_id": self.blind_trial_id,
            "bundle": self.bundle.document(),
            "packet": self.packet.document(),
            "verifier_report_sha256": self.verifier_report_sha256,
            "final_answer_sha256": self.final_answer_sha256,
            "prompt_sha256": self.prompt_sha256,
        }


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _controls(value: object) -> bool:
    if isinstance(value, str):
        return has_text_controls(value) and any(
            unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} and char not in "\n\r\t"
            for char in value
        )
    if isinstance(value, list):
        return any(_controls(item) for item in cast(list[object], value))
    if isinstance(value, dict):
        return any(
            _controls(key) or _controls(item)
            for key, item in cast(dict[object, object], value).items()
        )
    return False


def _object(value: object, code: str) -> dict[str, Any]:
    if type(value) is not dict or any(
        type(key) is not str for key in cast(dict[object, object], value)
    ):
        raise _HardIssue(code)
    return cast(dict[str, Any], value)


def _document(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = decode_json(raw, 32)
        if _controls(value):
            raise _HardIssue(code)
        canonical_bytes(value)
    except (DecodeError, CanonicalizationError, RecursionError) as exc:
        raise _HardIssue(code) from exc
    return _object(value, code)


def _read_path(path: Path, limit: int, code: str) -> bytes:
    try:
        return read_relative(path.parent, path.name, limit)
    except ValueError as exc:
        reason = str(exc)
        if reason == "FILE_LIMIT_EXCEEDED":
            raise _HardIssue(f"{code}_LIMIT_EXCEEDED") from exc
        if reason in {"UNSAFE_PATH", "FILE_NOT_REGULAR"}:
            raise _HardIssue(f"{code}_PATH_REFUSED") from exc
        if reason == "FILE_MISSING":
            raise _HardIssue(f"{code}_MISSING") from exc
        raise _HardIssue(f"{code}_UNREADABLE") from exc


def _relative_name(value: object, code: str) -> str:
    if type(value) is not str:
        raise _HardIssue(code)
    name = value
    path = PurePosixPath(name)
    if (
        not name
        or "\\" in name
        or path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or any(part in ("", ".") for part in path.parts)
        or any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in name)
    ):
        raise _HardIssue(code)
    return name


def _descriptor(value: object, limit: int, code: str) -> _Descriptor:
    row = _object(value, code)
    if set(row) != {"path", "bytes", "sha256"}:
        raise _HardIssue(code)
    path = _relative_name(row["path"], f"{code}_PATH_REFUSED")
    size, identity = row["bytes"], row["sha256"]
    if (
        type(size) is not int
        or not 0 <= size <= limit
        or type(identity) is not str
        or evaluation.DIGEST.fullmatch(identity) is None
    ):
        raise _HardIssue(code)
    return _Descriptor(path, size, identity)


def _local_descriptor(value: object, limit: int, code: str) -> _Descriptor:
    descriptor = _descriptor(value, limit, code)
    if len(PurePosixPath(descriptor.path).parts) != 1:
        raise _HardIssue(f"{code}_PATH_REFUSED")
    return descriptor


def _read_descriptor(
    root: Path, descriptor: _Descriptor, limit: int, *, missing_is_soft: bool
) -> tuple[bytes | None, str | None]:
    try:
        raw = read_relative(root, descriptor.path, limit)
    except ValueError as exc:
        reason = str(exc)
        if reason in {"UNSAFE_PATH", "FILE_NOT_REGULAR"}:
            raise _HardIssue("ARTIFACT_PATH_REFUSED") from exc
        if reason == "FILE_LIMIT_EXCEEDED":
            raise _HardIssue("ARTIFACT_LIMIT_EXCEEDED") from exc
        if reason == "FILE_MISSING" and missing_is_soft:
            return None, "ARTIFACT_MISSING"
        raise _HardIssue("ARTIFACT_UNREADABLE") from exc
    if len(raw) != descriptor.bytes or _sha256(raw) != descriptor.sha256:
        return None, "ARTIFACT_DIGEST_MISMATCH"
    return raw, None


def _strict_identity(document: dict[str, Any], keys: tuple[str, ...], code: str) -> None:
    if any(
        type(document.get(key)) is not str
        or evaluation.DIGEST.fullmatch(cast(str, document[key])) is None
        for key in keys
    ):
        raise _HardIssue(code)


def _load_evaluation(
    suite_path: Path, archive_root: Path, arm: object
) -> tuple[dict[str, list[dict[str, object]]], tuple[evaluation.Case, ...], str, dict[str, object]]:
    if arm not in evaluation.ARMS:
        raise _HardIssue("ARM_INVALID")
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive_root)
    cases = evaluation.load_suite(suite_path, tools, inputs)
    template = evaluation.trial_template(cases, cast(str, arm), ledger_digest)
    return tools, cases, ledger_digest, template


def _base_report(operation: Literal["prepare", "assemble"]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "operation": operation,
        "state": "not_adjudicable",
        "arm": None,
        "population": {
            "expected": 0,
            "execution_adjudicable": 0,
            "double_annotated": 0,
            "resolved": 0,
            "assembled": 0,
        },
        "inputs": {},
        "cases": [],
        "limitations": LIMITATIONS,
        "mapping": None,
        "trial": None,
    }


def _case_report(case_id: str, bundle_sha256: str | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "state": "not_adjudicable",
        "reasons": [],
        "bundle_sha256": bundle_sha256,
        "verifier_report_sha256": None,
    }


def _soft(row: dict[str, Any], reason: str) -> None:
    reasons = cast(list[str], row["reasons"])
    if reason not in reasons:
        reasons.append(reason)


def _verifier_failure(row: dict[str, Any], report: dict[str, Any]) -> None:
    raw_reasons = report.get("reasons")
    reason_rows = cast(list[object], raw_reasons) if isinstance(raw_reasons, list) else []
    if reason_rows and all(type(item) is str for item in reason_rows):
        for item in cast(list[str], reason_rows):
            _soft(row, f"VERIFIER_{item}")
    else:
        _soft(row, "VERIFIER_NOT_ADJUDICABLE")


def _canonical_file(document: object) -> bytes:
    return canonical_bytes(document) + b"\n"


def _write_private(directory_fd: int, name: str, raw: bytes) -> None:
    if name != PurePosixPath(name).name or name in {"", ".", ".."}:
        raise _HardIssue("OUTPUT_NAME_INVALID")
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise _HardIssue("UNSUPPORTED_PLATFORM")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    descriptor = -1
    try:
        descriptor = os.open(name, flags, 0o600, dir_fd=directory_fd)
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError as exc:
        raise _HardIssue("OUTPUT_WRITE_FAILED") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _new_output_directory(path: Path) -> int:
    if not hasattr(os, "O_DIRECTORY") or not hasattr(os, "O_NOFOLLOW"):
        raise _HardIssue("UNSUPPORTED_PLATFORM")
    directory_fd = -1
    try:
        path.mkdir(mode=OUTPUT_DIRECTORY_MODE, parents=False, exist_ok=False)
        directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(directory_fd)
    except (OSError, ValueError) as exc:
        if directory_fd >= 0:
            os.close(directory_fd)
        raise _HardIssue("OUTPUT_DIRECTORY_INVALID") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != OUTPUT_DIRECTORY_MODE:
        os.close(directory_fd)
        raise _HardIssue("OUTPUT_DIRECTORY_INVALID")
    return directory_fd


def _same_output_directory(path: Path, directory_fd: int) -> None:
    try:
        expected = os.fstat(directory_fd)
        observed = path.stat(follow_symlinks=False)
    except OSError as exc:
        raise _HardIssue("OUTPUT_DIRECTORY_CHANGED") from exc
    if not stat.S_ISDIR(observed.st_mode) or (expected.st_dev, expected.st_ino) != (
        observed.st_dev,
        observed.st_ino,
    ):
        raise _HardIssue("OUTPUT_DIRECTORY_CHANGED")


def _packet(
    blind_id: str,
    case: evaluation.Case,
    report: dict[str, Any],
    materials: execution.VerifiedMaterials,
    protocol_sha256: str,
) -> dict[str, Any]:
    wire_calls = cast(list[dict[str, Any]], report["wire_calls"])
    if len(wire_calls) != len(materials.calls):
        raise _HardIssue("VERIFIER_MATERIALS_INVALID")
    calls: list[dict[str, Any]] = []
    for wire, raw in zip(wire_calls, materials.calls, strict=True):
        if _sha256(raw.request) != wire.get("request_raw_sha256") or _sha256(
            raw.response
        ) != wire.get("response_raw_sha256"):
            raise _HardIssue("VERIFIER_MATERIALS_INVALID")
        calls.append(
            {
                "ordinal": wire["ordinal"],
                "name": wire["name"],
                "arguments": wire["arguments"],
                "request_raw_base64": base64.b64encode(raw.request).decode("ascii"),
                "response_raw_base64": base64.b64encode(raw.response).decode("ascii"),
            }
        )
    prompt_raw = case.prompt.encode("utf-8")
    return {
        "schema": PACKET_SCHEMA,
        "blind_trial_id": blind_id,
        "prompt": case.prompt,
        "prompt_sha256": _sha256(prompt_raw),
        "final_answer_base64": base64.b64encode(materials.final_answer).decode("ascii"),
        "calls": calls,
        "limitations": report["limitations"],
        "annotation_protocol_sha256": protocol_sha256,
    }


def _roster_digest(entries: list[_PreparedCase], protocol_sha256: str) -> str:
    rows = [
        {
            "blind_trial_id": row.blind_trial_id,
            "final_answer_sha256": row.final_answer_sha256,
            "prompt_sha256": row.prompt_sha256,
            "review_packet_sha256": row.packet.sha256,
        }
        for row in sorted(entries, key=lambda item: item.blind_trial_id)
    ]
    return digest_bytes(
        canonical_bytes({"annotation_protocol_sha256": protocol_sha256, "entries": rows})
    )


def _roster(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ROSTER_BYTES, "ROSTER")
    document = _document(raw, "ROSTER_INVALID")
    if (
        set(document)
        != {
            "schema",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "entries",
        }
        or document["schema"] != ROSTER_SCHEMA
    ):
        raise _HardIssue("ROSTER_INVALID")
    _strict_identity(
        document,
        ("suite_digest", "catalog_digest", "input_ledger_digest"),
        "ROSTER_INVALID",
    )
    entries = document["entries"]
    if not isinstance(entries, list) or len(cast(list[object], entries)) > MAX_ENTRIES:
        raise _HardIssue("ROSTER_INVALID")
    return document, raw


def prepare(  # noqa: C901, PLR0912, PLR0915 - one closed prepare transaction
    roster_path: Path,
    bundle_root: Path,
    suite_path: Path,
    archive_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Verify a complete arm and emit private blind packets plus mapping."""
    report = _base_report("prepare")
    roster, roster_raw = _roster(roster_path)
    arm = roster["arm"]
    _tools, cases, _ledger_digest, template = _load_evaluation(suite_path, archive_root, arm)
    selected = evaluation.selected_cases(cases, cast(str, arm))
    case_by_id = {case.case_id: case for case in selected}
    expected = set(case_by_id)
    report["arm"] = arm
    report["population"]["expected"] = len(selected)
    report["inputs"] = {"roster_sha256": _sha256(roster_raw)}
    identity_matches = all(
        roster[key] == template[key]
        for key in ("suite_digest", "catalog_digest", "input_ledger_digest")
    )

    roster_rows = cast(list[object], roster["entries"])
    by_case: dict[str, tuple[_Descriptor, dict[str, Any]]] = {}
    global_reasons: list[str] = []
    for value in roster_rows:
        row = _object(value, "ROSTER_INVALID")
        if set(row) != {"case_id", "bundle"}:
            raise _HardIssue("ROSTER_INVALID")
        case_id = row["case_id"]
        if type(case_id) is not str or evaluation.CASE_ID.fullmatch(case_id) is None:
            raise _HardIssue("ROSTER_INVALID")
        descriptor = _descriptor(
            row["bundle"], MAX_BUNDLE_MANIFEST_BYTES, "BUNDLE_DESCRIPTOR_INVALID"
        )
        case_report = _case_report(case_id, descriptor.sha256)
        if case_id in by_case:
            _soft(case_report, "DUPLICATE_CASE")
            global_reasons.append("DUPLICATE_CASE")
            cast(list[dict[str, Any]], report["cases"]).append(case_report)
            continue
        by_case[case_id] = descriptor, case_report
        cast(list[dict[str, Any]], report["cases"]).append(case_report)

    if not identity_matches:
        global_reasons.append("IDENTITY_MISMATCH")
    if set(by_case) != expected:
        global_reasons.append("ARM_INCOMPLETE")
    prepared: list[tuple[_PreparedCase, bytes]] = []
    used_ids: set[str] = set()
    total_bundle_bytes = 0
    protocol_raw = _read_path(ANNOTATION_PROTOCOL, MAX_PROTOCOL_BYTES, "ANNOTATION_PROTOCOL")
    protocol_sha256 = _sha256(protocol_raw)

    for case_id, (descriptor, case_report) in by_case.items():
        if case_id not in expected:
            _soft(case_report, "UNEXPECTED_CASE")
            continue
        if not identity_matches:
            _soft(case_report, "IDENTITY_MISMATCH")
            continue
        bundle_raw, reason = _read_descriptor(
            bundle_root, descriptor, MAX_BUNDLE_MANIFEST_BYTES, missing_is_soft=True
        )
        if reason is not None:
            _soft(case_report, reason)
            continue
        assert bundle_raw is not None
        bundle_path = bundle_root / descriptor.path
        verifier_report, materials = execution.verify_materials(
            bundle_path, suite_path, archive_root
        )
        # A failed verifier can have read every bounded artifact before rejecting it.
        total_bundle_bytes += len(bundle_raw) + (
            materials.artifact_bytes if materials else FAILED_BUNDLE_ARTIFACT_BOUND
        )
        if total_bundle_bytes > MAX_TOTAL_BUNDLE_BYTES:
            raise _HardIssue("BUNDLE_TOTAL_LIMIT_EXCEEDED")
        verifier_digest = digest_bytes(canonical_bytes(verifier_report))
        case_report["verifier_report_sha256"] = verifier_digest
        if verifier_report.get("state") != "adjudicable" or materials is None:
            _verifier_failure(case_report, verifier_report)
            continue
        if (
            materials.manifest_bytes != descriptor.bytes
            or materials.manifest_sha256 != descriptor.sha256
        ):
            _soft(case_report, "BUNDLE_IDENTITY_CHANGED")
            continue
        if (
            verifier_report.get("case_id") != case_id
            or verifier_report.get("arm") != arm
            or any(
                verifier_report.get(key) != template[key]
                for key in ("suite_digest", "catalog_digest", "input_ledger_digest")
            )
        ):
            _soft(case_report, "VERIFIER_IDENTITY_MISMATCH")
            continue
        projection_value = verifier_report.get("trial_projection")
        if type(projection_value) is not dict:
            _soft(case_report, "VERIFIER_PROJECTION_INVALID")
            continue
        projection = cast(dict[str, Any], projection_value)
        if projection.get("case_id") != case_id:
            _soft(case_report, "VERIFIER_PROJECTION_INVALID")
            continue
        blind_id = "bt_" + secrets.token_hex(16)
        while blind_id in used_ids:
            blind_id = "bt_" + secrets.token_hex(16)
        used_ids.add(blind_id)
        packet_document = _packet(
            blind_id, case_by_id[case_id], verifier_report, materials, protocol_sha256
        )
        packet_raw = _canonical_file(packet_document)
        if len(packet_raw) > MAX_PACKET_BYTES:
            raise _HardIssue("PACKET_LIMIT_EXCEEDED")
        packet_descriptor = _Descriptor(f"{blind_id}.json", len(packet_raw), _sha256(packet_raw))
        prepared_case = _PreparedCase(
            case_id,
            blind_id,
            descriptor,
            packet_descriptor,
            verifier_digest,
            _sha256(materials.final_answer),
            _sha256(case_by_id[case_id].prompt.encode("utf-8")),
        )
        prepared.append((prepared_case, packet_raw))
        case_report["state"] = "adjudicable"
        report["population"]["execution_adjudicable"] += 1

    case_rows = cast(list[dict[str, Any]], report["cases"])
    if (
        global_reasons
        or len(prepared) != len(selected)
        or any(row["state"] != "adjudicable" for row in case_rows)
    ):
        report["reasons"] = sorted(set(global_reasons))
        return report

    total_packet_bytes = sum(len(raw) for _row, raw in prepared)
    if total_packet_bytes > MAX_TOTAL_PACKET_BYTES:
        raise _HardIssue("PACKET_TOTAL_LIMIT_EXCEEDED")
    output_fd = _new_output_directory(output_dir)
    protocol_name = ANNOTATION_PROTOCOL.name
    try:
        _write_private(output_fd, protocol_name, protocol_raw)
        for row, raw in prepared:
            _write_private(output_fd, row.packet.path, raw)
        rows = [row for row, _raw in prepared]
        roster_digest = _roster_digest(rows, protocol_sha256)
        mapping = {
            "schema": MAPPING_SCHEMA,
            "arm": arm,
            "suite_digest": template["suite_digest"],
            "catalog_digest": template["catalog_digest"],
            "input_ledger_digest": template["input_ledger_digest"],
            "blind_roster_digest": roster_digest,
            "annotation_protocol": {
                "path": protocol_name,
                "bytes": len(protocol_raw),
                "sha256": protocol_sha256,
            },
            "entries": [row.mapping_row() for row in rows],
        }
        mapping_raw = _canonical_file(mapping)
        if len(mapping_raw) > MAX_MAPPING_BYTES:
            raise _HardIssue("MAPPING_LIMIT_EXCEEDED")
        mapping_path = output_dir / "blind-mapping.v1.json"
        _write_private(output_fd, mapping_path.name, mapping_raw)
        _same_output_directory(output_dir, output_fd)
    finally:
        os.close(output_fd)
    report["state"] = "ready_for_review"
    report["mapping"] = str(mapping_path)
    report["inputs"]["mapping_sha256"] = _sha256(mapping_raw)
    return report


def _mapping(path: Path) -> tuple[dict[str, Any], bytes]:  # noqa: C901
    raw = _read_path(path, MAX_MAPPING_BYTES, "MAPPING")
    document = _document(raw, "MAPPING_INVALID")
    if (
        set(document)
        != {
            "schema",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "blind_roster_digest",
            "annotation_protocol",
            "entries",
        }
        or document["schema"] != MAPPING_SCHEMA
    ):
        raise _HardIssue("MAPPING_INVALID")
    _strict_identity(
        document,
        (
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "blind_roster_digest",
        ),
        "MAPPING_INVALID",
    )
    _local_descriptor(
        document["annotation_protocol"], MAX_PROTOCOL_BYTES, "PROTOCOL_DESCRIPTOR_INVALID"
    )
    entries = document["entries"]
    if not isinstance(entries, list) or len(cast(list[object], entries)) > MAX_ENTRIES:
        raise _HardIssue("MAPPING_INVALID")
    return document, raw


def _mapping_entry(value: object) -> _PreparedCase:
    row = _object(value, "MAPPING_INVALID")
    if set(row) != {
        "case_id",
        "blind_trial_id",
        "bundle",
        "packet",
        "verifier_report_sha256",
        "final_answer_sha256",
        "prompt_sha256",
    }:
        raise _HardIssue("MAPPING_INVALID")
    case_id, blind_id = row["case_id"], row["blind_trial_id"]
    if (
        type(case_id) is not str
        or evaluation.CASE_ID.fullmatch(case_id) is None
        or type(blind_id) is not str
        or BLIND_ID.fullmatch(blind_id) is None
    ):
        raise _HardIssue("MAPPING_INVALID")
    _strict_identity(
        row,
        ("verifier_report_sha256", "final_answer_sha256", "prompt_sha256"),
        "MAPPING_INVALID",
    )
    return _PreparedCase(
        case_id,
        blind_id,
        _descriptor(row["bundle"], MAX_BUNDLE_MANIFEST_BYTES, "BUNDLE_DESCRIPTOR_INVALID"),
        _local_descriptor(row["packet"], MAX_PACKET_BYTES, "PACKET_DESCRIPTOR_INVALID"),
        row["verifier_report_sha256"],
        row["final_answer_sha256"],
        row["prompt_sha256"],
    )


def _packet_matches(
    raw: bytes,
    mapped: _PreparedCase,
    case: evaluation.Case,
    verifier_report: dict[str, Any],
    materials: execution.VerifiedMaterials,
    protocol_sha256: str,
) -> bool:
    observed = _document(raw, "PACKET_INVALID")
    expected = _packet(mapped.blind_trial_id, case, verifier_report, materials, protocol_sha256)
    return canonical_bytes(observed) == canonical_bytes(expected)


def _reviewer_id(value: object, code: str) -> str:
    if type(value) is not str or REVIEWER_ID.fullmatch(value) is None:
        raise _HardIssue(code)
    return value


def _claims(value: object, code: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise _HardIssue(code)
    rows = cast(list[object], value)
    if any(type(item) is not str or item not in evaluation.CLAIMS for item in rows) or len(
        rows
    ) != len(set(cast(list[str], rows))):
        raise _HardIssue(code)
    return tuple(sorted(cast(list[str], rows)))


def _annotation_row(value: object) -> tuple[str, str, str, str, tuple[str, ...]]:
    row = _object(value, "ANNOTATIONS_INVALID")
    if set(row) != {
        "blind_trial_id",
        "final_answer_sha256",
        "prompt_sha256",
        "review_packet_sha256",
        "claims",
    }:
        raise _HardIssue("ANNOTATIONS_INVALID")
    blind_id = row["blind_trial_id"]
    if type(blind_id) is not str or BLIND_ID.fullmatch(blind_id) is None:
        raise _HardIssue("ANNOTATIONS_INVALID")
    _strict_identity(
        row,
        ("final_answer_sha256", "prompt_sha256", "review_packet_sha256"),
        "ANNOTATIONS_INVALID",
    )
    return (
        blind_id,
        row["final_answer_sha256"],
        row["prompt_sha256"],
        row["review_packet_sha256"],
        _claims(row["claims"], "ANNOTATION_CLAIMS_INVALID"),
    )


def _annotations(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ANNOTATION_BYTES, "ANNOTATIONS")
    document = _document(raw, "ANNOTATIONS_INVALID")
    if (
        set(document)
        != {
            "schema",
            "blind_roster_digest",
            "annotation_protocol_sha256",
            "annotator_id",
            "annotations",
        }
        or document["schema"] != ANNOTATIONS_SCHEMA
    ):
        raise _HardIssue("ANNOTATIONS_INVALID")
    _strict_identity(
        document,
        ("blind_roster_digest", "annotation_protocol_sha256"),
        "ANNOTATIONS_INVALID",
    )
    _reviewer_id(document["annotator_id"], "ANNOTATOR_ID_INVALID")
    annotations = document["annotations"]
    if not isinstance(annotations, list) or len(cast(list[object], annotations)) > MAX_ANNOTATIONS:
        raise _HardIssue("ANNOTATIONS_INVALID")
    return document, raw


def _index_annotations(
    document: dict[str, Any], expected: dict[str, _PreparedCase]
) -> tuple[dict[str, tuple[str, ...]], list[str]]:
    result: dict[str, tuple[str, ...]] = {}
    reasons: list[str] = []
    for value in cast(list[object], document["annotations"]):
        blind_id, answer, prompt, packet, claims = _annotation_row(value)
        mapped = expected.get(blind_id)
        if blind_id in result:
            reasons.append("DUPLICATE_ANNOTATION")
            continue
        if mapped is None:
            reasons.append("UNEXPECTED_ANNOTATION")
            continue
        if (
            answer != mapped.final_answer_sha256
            or prompt != mapped.prompt_sha256
            or packet != mapped.packet.sha256
        ):
            reasons.append("ANNOTATION_BINDING_MISMATCH")
            continue
        result[blind_id] = claims
    if set(result) != set(expected):
        reasons.append("ANNOTATION_ROSTER_INCOMPLETE")
    return result, reasons


def _rationale(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_RATIONALE_CHARS or not value.strip():
        raise _HardIssue("ADJUDICATION_RATIONALE_INVALID")
    rationale = value
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} and char not in "\n "
        for char in rationale
    ):
        raise _HardIssue("ADJUDICATION_RATIONALE_INVALID")
    return rationale


def _adjudication(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ADJUDICATION_BYTES, "ADJUDICATION")
    document = _document(raw, "ADJUDICATION_INVALID")
    if (
        set(document)
        != {
            "schema",
            "blind_roster_digest",
            "annotation_protocol_sha256",
            "annotation_sha256",
            "adjudicator_id",
            "resolutions",
        }
        or document["schema"] != ADJUDICATION_SCHEMA
    ):
        raise _HardIssue("ADJUDICATION_INVALID")
    _strict_identity(
        document,
        ("blind_roster_digest", "annotation_protocol_sha256"),
        "ADJUDICATION_INVALID",
    )
    raw_digests = document["annotation_sha256"]
    digests = cast(list[object], raw_digests) if isinstance(raw_digests, list) else []
    if (
        not isinstance(raw_digests, list)
        or len(digests) != PAIRED_REVIEWER_COUNT
        or any(
            type(item) is not str or evaluation.DIGEST.fullmatch(item) is None for item in digests
        )
        or cast(list[str], digests) != sorted(cast(list[str], digests))
        or len(set(cast(list[str], digests))) != PAIRED_REVIEWER_COUNT
    ):
        raise _HardIssue("ADJUDICATION_INVALID")
    _reviewer_id(document["adjudicator_id"], "ADJUDICATOR_ID_INVALID")
    resolutions = document["resolutions"]
    if not isinstance(resolutions, list) or len(cast(list[object], resolutions)) > MAX_RESOLUTIONS:
        raise _HardIssue("ADJUDICATION_INVALID")
    return document, raw


def _resolution_row(value: object) -> tuple[str, str, str, str, tuple[str, ...]]:
    row = _object(value, "ADJUDICATION_INVALID")
    if set(row) != {
        "blind_trial_id",
        "final_answer_sha256",
        "prompt_sha256",
        "review_packet_sha256",
        "claims",
        "rationale",
    }:
        raise _HardIssue("ADJUDICATION_INVALID")
    blind_id = row["blind_trial_id"]
    if type(blind_id) is not str or BLIND_ID.fullmatch(blind_id) is None:
        raise _HardIssue("ADJUDICATION_INVALID")
    _strict_identity(
        row,
        ("final_answer_sha256", "prompt_sha256", "review_packet_sha256"),
        "ADJUDICATION_INVALID",
    )
    _rationale(row["rationale"])
    return (
        blind_id,
        row["final_answer_sha256"],
        row["prompt_sha256"],
        row["review_packet_sha256"],
        _claims(row["claims"], "ADJUDICATION_CLAIMS_INVALID"),
    )


def _resolve(  # noqa: C901, PLR0913, PLR0917 - closed adjudication identity contract
    adjudication_path: Path | None,
    disagreements: set[str],
    mapped: dict[str, _PreparedCase],
    annotation_digests: list[str],
    reviewer_ids: set[str],
    roster_digest: str,
    protocol_sha256: str,
) -> tuple[dict[str, tuple[str, ...]], list[str], str | None]:
    if not disagreements:
        return {}, (["UNNEEDED_ADJUDICATION"] if adjudication_path is not None else []), None
    if adjudication_path is None:
        return {}, ["ADJUDICATION_MISSING"], None
    try:
        document, raw = _adjudication(adjudication_path)
    except _HardIssue as exc:
        if str(exc) == "ADJUDICATION_MISSING":
            return {}, ["ADJUDICATION_MISSING"], None
        raise
    reasons: list[str] = []
    if (
        document["blind_roster_digest"] != roster_digest
        or document["annotation_protocol_sha256"] != protocol_sha256
        or document["annotation_sha256"] != annotation_digests
    ):
        reasons.append("ADJUDICATION_BINDING_MISMATCH")
    adjudicator = cast(str, document["adjudicator_id"])
    if adjudicator in reviewer_ids:
        reasons.append("ADJUDICATOR_NOT_DISTINCT")
    resolutions: dict[str, tuple[str, ...]] = {}
    for value in cast(list[object], document["resolutions"]):
        blind_id, answer, prompt, packet, claims = _resolution_row(value)
        expected = mapped.get(blind_id)
        if blind_id in resolutions:
            reasons.append("DUPLICATE_RESOLUTION")
            continue
        if blind_id not in disagreements or expected is None:
            reasons.append("UNEXPECTED_RESOLUTION")
            continue
        if (
            answer != expected.final_answer_sha256
            or prompt != expected.prompt_sha256
            or packet != expected.packet.sha256
        ):
            reasons.append("ADJUDICATION_BINDING_MISMATCH")
            continue
        resolutions[blind_id] = claims
    if set(resolutions) != disagreements:
        reasons.append("ADJUDICATION_INCOMPLETE")
    return resolutions, reasons, _sha256(raw)


def assemble(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917
    mapping_path: Path,
    bundle_root: Path,
    annotation_a: Path,
    annotation_b: Path,
    adjudication_path: Path | None,
    suite_path: Path,
    archive_root: Path,
) -> dict[str, Any]:
    """Reverify blinded materials and return one complete scorer trial document."""
    output = _base_report("assemble")
    mapping, mapping_raw = _mapping(mapping_path)
    arm = mapping["arm"]
    tools, cases, ledger_digest, template = _load_evaluation(suite_path, archive_root, arm)
    selected = evaluation.selected_cases(cases, cast(str, arm))
    case_by_id = {case.case_id: case for case in selected}
    output["arm"] = arm
    output["population"]["expected"] = len(selected)
    output["inputs"] = {"mapping_sha256": _sha256(mapping_raw)}
    reasons: list[str] = []
    if mapping["arm"] != template["arm"] or any(
        mapping[key] != template[key]
        for key in ("suite_digest", "catalog_digest", "input_ledger_digest")
    ):
        reasons.append("IDENTITY_MISMATCH")

    entries = [_mapping_entry(value) for value in cast(list[object], mapping["entries"])]
    by_case: dict[str, _PreparedCase] = {}
    by_blind: dict[str, _PreparedCase] = {}
    for entry in entries:
        case_report = _case_report(entry.case_id, entry.bundle.sha256)
        if entry.case_id in by_case or entry.blind_trial_id in by_blind:
            _soft(case_report, "DUPLICATE_MAPPING_ENTRY")
            reasons.append("DUPLICATE_MAPPING_ENTRY")
        else:
            by_case[entry.case_id] = entry
            by_blind[entry.blind_trial_id] = entry
        cast(list[dict[str, Any]], output["cases"]).append(case_report)
    if set(by_case) != set(case_by_id):
        reasons.append("ARM_INCOMPLETE")

    protocol_descriptor = _descriptor(
        mapping["annotation_protocol"], MAX_PROTOCOL_BYTES, "PROTOCOL_DESCRIPTOR_INVALID"
    )
    protocol_raw, protocol_reason = _read_descriptor(
        mapping_path.parent, protocol_descriptor, MAX_PROTOCOL_BYTES, missing_is_soft=True
    )
    if protocol_reason is not None:
        reasons.append(protocol_reason)
    protocol_sha256 = protocol_descriptor.sha256
    if _roster_digest(entries, protocol_sha256) != mapping["blind_roster_digest"]:
        reasons.append("BLIND_ROSTER_DIGEST_MISMATCH")

    total_packet_bytes = 0
    total_bundle_bytes = 0
    projection: dict[str, tuple[str, ...]] = {}
    case_reports = {
        cast(str, row["case_id"]): row for row in cast(list[dict[str, Any]], output["cases"])
    }
    for case_id, entry in by_case.items():
        row = case_reports[case_id]
        case = case_by_id.get(case_id)
        if case is None:
            _soft(row, "UNEXPECTED_CASE")
            continue
        bundle_raw, bundle_reason = _read_descriptor(
            bundle_root, entry.bundle, MAX_BUNDLE_MANIFEST_BYTES, missing_is_soft=True
        )
        if bundle_reason is not None:
            _soft(row, bundle_reason)
            continue
        assert bundle_raw is not None
        verifier_report, materials = execution.verify_materials(
            bundle_root / entry.bundle.path, suite_path, archive_root
        )
        total_bundle_bytes += len(bundle_raw) + (
            materials.artifact_bytes if materials else FAILED_BUNDLE_ARTIFACT_BOUND
        )
        if total_bundle_bytes > MAX_TOTAL_BUNDLE_BYTES:
            raise _HardIssue("BUNDLE_TOTAL_LIMIT_EXCEEDED")
        verifier_digest = digest_bytes(canonical_bytes(verifier_report))
        row["verifier_report_sha256"] = verifier_digest
        if verifier_report.get("state") != "adjudicable" or materials is None:
            _verifier_failure(row, verifier_report)
            continue
        if (
            materials.manifest_bytes != entry.bundle.bytes
            or materials.manifest_sha256 != entry.bundle.sha256
        ):
            _soft(row, "BUNDLE_IDENTITY_CHANGED")
            continue
        if (
            verifier_digest != entry.verifier_report_sha256
            or verifier_report.get("case_id") != case_id
            or verifier_report.get("arm") != arm
        ):
            _soft(row, "VERIFIER_REPORT_MISMATCH")
            continue
        if (
            _sha256(materials.final_answer) != entry.final_answer_sha256
            or _sha256(case.prompt.encode("utf-8")) != entry.prompt_sha256
        ):
            _soft(row, "MATERIAL_IDENTITY_MISMATCH")
            continue
        packet_raw, packet_reason = _read_descriptor(
            mapping_path.parent, entry.packet, MAX_PACKET_BYTES, missing_is_soft=True
        )
        if packet_reason is not None:
            _soft(row, packet_reason)
            continue
        assert packet_raw is not None
        total_packet_bytes += len(packet_raw)
        if total_packet_bytes > MAX_TOTAL_PACKET_BYTES:
            raise _HardIssue("PACKET_TOTAL_LIMIT_EXCEEDED")
        if protocol_raw is None or not _packet_matches(
            packet_raw, entry, case, verifier_report, materials, protocol_sha256
        ):
            _soft(row, "PACKET_MISMATCH")
            continue
        projection_value = verifier_report.get("trial_projection")
        if type(projection_value) is not dict:
            _soft(row, "VERIFIER_PROJECTION_INVALID")
            continue
        trial_projection = cast(dict[str, Any], projection_value)
        if trial_projection.get("case_id") != case_id:
            _soft(row, "VERIFIER_PROJECTION_INVALID")
            continue
        calls = trial_projection.get("calls")
        call_rows = cast(list[object], calls) if isinstance(calls, list) else []
        if not isinstance(calls, list) or any(type(name) is not str for name in call_rows):
            _soft(row, "VERIFIER_PROJECTION_INVALID")
            continue
        projection[case_id] = tuple(cast(list[str], call_rows))
        row["state"] = "adjudicable"
        output["population"]["execution_adjudicable"] += 1

    try:
        document_a, raw_a = _annotations(annotation_a)
        document_b, raw_b = _annotations(annotation_b)
    except _HardIssue as exc:
        if str(exc) == "ANNOTATIONS_MISSING":
            output["reasons"] = ["ANNOTATIONS_MISSING"]
            return output
        raise
    annotation_count = len(cast(list[object], document_a["annotations"])) + len(
        cast(list[object], document_b["annotations"])
    )
    if annotation_count > MAX_ANNOTATIONS:
        raise _HardIssue("ANNOTATION_TOTAL_LIMIT_EXCEEDED")
    digest_a, digest_b = _sha256(raw_a), _sha256(raw_b)
    output["inputs"].update(
        annotation_a_sha256=digest_a,
        annotation_b_sha256=digest_b,
    )
    if digest_a == digest_b:
        reasons.append("ANNOTATION_FILES_NOT_DISTINCT")
    reviewer_a = cast(str, document_a["annotator_id"])
    reviewer_b = cast(str, document_b["annotator_id"])
    if reviewer_a == reviewer_b:
        reasons.append("ANNOTATORS_NOT_DISTINCT")
    for document in (document_a, document_b):
        if (
            document["blind_roster_digest"] != mapping["blind_roster_digest"]
            or document["annotation_protocol_sha256"] != protocol_sha256
        ):
            reasons.append("ANNOTATION_BINDING_MISMATCH")
    claims_a, reasons_a = _index_annotations(document_a, by_blind)
    claims_b, reasons_b = _index_annotations(document_b, by_blind)
    reasons.extend(reasons_a)
    reasons.extend(reasons_b)
    double = set(claims_a) & set(claims_b)
    output["population"]["double_annotated"] = len(double)
    disagreements = {blind_id for blind_id in double if claims_a[blind_id] != claims_b[blind_id]}
    annotation_digests = sorted([digest_a, digest_b])
    resolutions, resolution_reasons, adjudication_digest = _resolve(
        adjudication_path,
        disagreements,
        by_blind,
        annotation_digests,
        {reviewer_a, reviewer_b},
        cast(str, mapping["blind_roster_digest"]),
        protocol_sha256,
    )
    reasons.extend(resolution_reasons)
    if adjudication_digest is not None:
        output["inputs"]["adjudication_sha256"] = adjudication_digest
    output["population"]["resolved"] = len(resolutions)

    if (
        reasons
        or len(projection) != len(selected)
        or len(double) != len(selected)
        or any(row["state"] != "adjudicable" for row in cast(list[dict[str, Any]], output["cases"]))
    ):
        output["reasons"] = sorted(set(reasons))
        return output

    trial = evaluation.trial_template(cases, cast(str, arm), ledger_digest)
    trial_rows: list[dict[str, object]] = []
    for case in selected:
        mapped = by_case[case.case_id]
        claims = resolutions.get(mapped.blind_trial_id, claims_a[mapped.blind_trial_id])
        trial_rows.append(
            {
                "case_id": case.case_id,
                "calls": list(projection[case.case_id]),
                "claims": list(claims),
            }
        )
    trial["trials"] = trial_rows
    validated = evaluation.read_trials(trial, cases, cast(str, arm), ledger_digest, tools)
    if len(validated) != len(selected):
        raise _HardIssue("TRIAL_INCOMPLETE")
    output["population"]["assembled"] = len(validated)
    output["state"] = "ready_for_scoring"
    output["trial"] = trial
    return output


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare_parser = actions.add_parser("prepare")
    prepare_parser.add_argument("--roster", type=Path, required=True)
    prepare_parser.add_argument("--bundle-root", type=Path, required=True)
    prepare_parser.add_argument("--suite", type=Path, required=True)
    prepare_parser.add_argument("--archive-root", type=Path, required=True)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    assemble_parser = actions.add_parser("assemble")
    assemble_parser.add_argument("--mapping", type=Path, required=True)
    assemble_parser.add_argument("--bundle-root", type=Path, required=True)
    assemble_parser.add_argument("--annotator-a", type=Path, required=True)
    assemble_parser.add_argument("--annotator-b", type=Path, required=True)
    assemble_parser.add_argument("--adjudication", type=Path)
    assemble_parser.add_argument("--suite", type=Path, required=True)
    assemble_parser.add_argument("--archive-root", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "prepare":
            report = prepare(
                args.roster, args.bundle_root, args.suite, args.archive_root, args.output_dir
            )
        else:
            report = assemble(
                args.mapping,
                args.bundle_root,
                args.annotator_a,
                args.annotator_b,
                args.adjudication,
                args.suite,
                args.archive_root,
            )
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

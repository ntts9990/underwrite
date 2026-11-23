# pyright: reportPrivateUsage=false
"""Prepare and assess the three-case A3 factual pilot without scoring output.

The pilot rubric is an external input kept outside the repository. It alone names
the cases, expected tool arguments, and fact targets; this module holds no
case-specific values and checks each target against the frozen tool catalog.
The pilot binds blinded human relation labels to canonical successful MCP
results already checked by the execution verifier.  It proves local artifact
consistency only; it does not authenticate execution or reviewers, and it does
not make a model-performance or adoption decision.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from scripts import assemble_agent_surface_trial_v2 as blind
from scripts import evaluate_agent_surface_v2 as evaluation
from scripts import verify_agent_surface_execution_v2 as execution
from underwrite._json import DecodeError, decode_json
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator

ROOT = Path(__file__).resolve().parents[1]
ANNOTATION_PROTOCOL = ROOT / "fixtures/agent-surface/factual-annotation-protocol.v1.md"

RUBRIC_SCHEMA = "underwrite.agent_surface_factual_rubric.v1"
ROSTER_SCHEMA = "underwrite.agent_surface_factual_bundle_roster.v1"
MAPPING_SCHEMA = "underwrite.agent_surface_factual_blind_mapping.v1"
PACKET_SCHEMA = "underwrite.agent_surface_factual_blind_packet.v1"
ANNOTATIONS_SCHEMA = "underwrite.agent_surface_factual_annotations.v1"
ADJUDICATION_SCHEMA = "underwrite.agent_surface_factual_adjudication.v1"
REPORT_SCHEMA = "underwrite.agent_surface_factual_pilot_report.v1"

MAX_RUBRIC_BYTES = 64 * 1024
MAX_ROSTER_BYTES = 64 * 1024
MAX_MAPPING_BYTES = 64 * 1024
MAX_PROTOCOL_BYTES = 64 * 1024
MAX_PACKET_BYTES = 4 * 1024 * 1024
MAX_TOTAL_PACKET_BYTES = 12 * 1024 * 1024
MAX_ANNOTATION_BYTES = 64 * 1024
MAX_ADJUDICATION_BYTES = 64 * 1024
MAX_RATIONALE_CHARS = 1000
MAX_BUNDLE_MANIFEST_BYTES = execution.MAX_BUNDLE_BYTES
FAILED_BUNDLE_ARTIFACT_BOUND = sum(limit + 1 for limit in execution.ARTIFACT_LIMITS.values())
MAX_TOTAL_BUNDLE_BYTES = 3 * (execution.MAX_BUNDLE_BYTES + FAILED_BUNDLE_ARTIFACT_BOUND)

CASE_COUNT = 3
FACT_COUNT = 4
MAX_ARGUMENTS = 8
MAX_ARGUMENT_CHARS = 1024
ARGUMENT_NAME = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
TOOL_NAME = re.compile(r"[a-z][a-z0-9_]{1,63}\Z")
CONTRACT_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\.v[1-9][0-9]{0,3}\Z")
CONTRACT_ID_PREFIX = "urn:underwrite:contract:"
ARCHIVE_INPUT_PREFIX = "inputs/"
SCHEMA_BRANCHES = ("allOf", "anyOf", "oneOf", "then", "else")
PAIRED_REVIEWER_COUNT = 2
BLIND_TRIAL_ID = re.compile(r"bt_[0-9a-f]{32}\Z")
BLIND_FACT_ID = re.compile(r"bf_[0-9a-f]{32}\Z")
REVIEWER_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{1,79}\Z")
RELATIONS = frozenset({"supported", "contradicted", "partial", "not_asserted", "indeterminate"})
COMPARISONS = frozenset({"exact_scalar", "canonical_json_value"})
PUBLIC_RELATIONS = {
    "supported": "reviewed_supported",
    "contradicted": "contradicted",
    "partial": "partial",
    "not_asserted": "omitted",
    "indeterminate": "indeterminate",
}
LIMITATIONS = [
    "CONTENT_IDENTITY_NOT_PROVENANCE",
    "REVIEWER_IDENTITY_NOT_AUTHENTICATED",
    "REVIEWER_INDEPENDENCE_NOT_PROVEN",
    "BLINDING_OPERATION_NOT_PROVEN",
    "EXECUTION_PROVENANCE_NOT_AUTHENTICATED",
    "SEMANTIC_JUDGMENT_NOT_VERIFIED",
    "PERFORMANCE_NOT_EVALUATED",
]


class _HardIssue(ValueError):
    pass


@dataclass(frozen=True)
class _Target:
    target_id: str
    call_ordinal: int
    tool: str
    result_schema: str
    json_pointer: str
    comparison: Literal["exact_scalar", "canonical_json_value"]


@dataclass(frozen=True)
class _RubricCase:
    case_id: str
    expected_arguments: dict[str, object]
    targets: tuple[_Target, ...]


@dataclass(frozen=True)
class _PreparedFact:
    target: _Target
    blind_fact_id: str
    canonical_value: bytes
    structured_content_sha256: str

    def mapping_row(self) -> dict[str, object]:
        return {
            "target_id": self.target.target_id,
            "blind_fact_id": self.blind_fact_id,
            "call_ordinal": self.target.call_ordinal,
            "structured_content_sha256": self.structured_content_sha256,
            "canonical_value_sha256": digest_bytes(self.canonical_value),
        }

    def packet_row(self) -> dict[str, object]:
        return {
            "blind_fact_id": self.blind_fact_id,
            "call_ordinal": self.target.call_ordinal,
            "json_pointer": self.target.json_pointer,
            "comparison": self.target.comparison,
        }


@dataclass(frozen=True)
class _PreparedCase:
    case_id: str
    blind_trial_id: str
    bundle: blind._Descriptor
    packet: blind._Descriptor
    verifier_report_sha256: str
    request_raw_sha256: str
    response_raw_sha256: str
    final_answer_sha256: str
    facts: tuple[_PreparedFact, ...]

    def mapping_row(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "blind_trial_id": self.blind_trial_id,
            "bundle": self.bundle.document(),
            "packet": self.packet.document(),
            "verifier_report_sha256": self.verifier_report_sha256,
            "request_raw_sha256": self.request_raw_sha256,
            "response_raw_sha256": self.response_raw_sha256,
            "final_answer_sha256": self.final_answer_sha256,
            "facts": [fact.mapping_row() for fact in self.facts],
        }


@dataclass(frozen=True)
class _Annotation:
    relation: str
    answer_span: tuple[int, int] | None


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _canonical_file(document: object) -> bytes:
    return canonical_bytes(document) + b"\n"


def _strict_document(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = decode_json(raw, 32)
        canonical_bytes(value)
        if _controls(value):
            raise _HardIssue(code)
    except (DecodeError, CanonicalizationError, RecursionError) as exc:
        raise _HardIssue(code) from exc
    if type(value) is not dict or any(
        type(key) is not str for key in cast(dict[object, object], value)
    ):
        raise _HardIssue(code)
    return cast(dict[str, Any], value)


def _controls(value: object) -> bool:
    if isinstance(value, str):
        return any(
            unicodedata.category(character) in {"Cc", "Cf", "Zl", "Zp"}
            and character not in "\n\r\t"
            for character in value
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


def _read_path(path: Path, limit: int, code: str) -> bytes:
    try:
        return blind._read_path(path, limit, code)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc


def _read_descriptor(
    root: Path, descriptor: blind._Descriptor, limit: int, *, missing_is_soft: bool
) -> tuple[bytes | None, str | None]:
    try:
        return blind._read_descriptor(root, descriptor, limit, missing_is_soft=missing_is_soft)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc


def _descriptor(value: object, limit: int, code: str) -> blind._Descriptor:
    try:
        return blind._descriptor(value, limit, code)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc


def _local_descriptor(value: object, limit: int, code: str) -> blind._Descriptor:
    try:
        return blind._local_descriptor(value, limit, code)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc


def _identity(document: dict[str, Any], keys: tuple[str, ...], code: str) -> None:
    if any(
        type(document.get(key)) is not str
        or evaluation.DIGEST.fullmatch(cast(str, document[key])) is None
        for key in keys
    ):
        raise _HardIssue(code)


def _target(value: object) -> _Target:
    row = _object(value, "RUBRIC_INVALID")
    if set(row) != {
        "target_id",
        "call_ordinal",
        "tool",
        "result_schema",
        "json_pointer",
        "comparison",
        "requirement",
    }:
        raise _HardIssue("RUBRIC_INVALID")
    target_id = row["target_id"]
    tool = row["tool"]
    schema = row["result_schema"]
    pointer = row["json_pointer"]
    comparison = row["comparison"]
    if (
        type(target_id) is not str
        or not re.fullmatch(r"[a-z][a-z0-9_]{1,63}", target_id)
        or row["call_ordinal"] != 0
        or type(row["call_ordinal"]) is not int
        or type(tool) is not str
        or TOOL_NAME.fullmatch(tool) is None
        or type(schema) is not str
        or CONTRACT_NAME.fullmatch(schema) is None
        or type(pointer) is not str
        or not pointer.startswith("/")
        or type(comparison) is not str
        or comparison not in COMPARISONS
        or row["requirement"] != "must_assert"
    ):
        raise _HardIssue("RUBRIC_INVALID")
    return _Target(
        target_id,
        0,
        tool,
        schema,
        pointer,
        cast(Literal["exact_scalar", "canonical_json_value"], comparison),
    )


def _unsafe_text(value: str) -> bool:
    """Refuse absolute or non-normalized path spellings that would dodge input checks."""
    return (
        not 1 <= len(value) <= MAX_ARGUMENT_CHARS
        or value.startswith("/")
        or "\\" in value
        or value in {".", ".."}
        or ("/" in value and any(part in {"", ".", ".."} for part in value.split("/")))
    )


def _arguments(value: object) -> dict[str, object]:
    """Accept scalar tool arguments; file paths must be archive-relative."""
    arguments = _object(value, "RUBRIC_INVALID")
    if not 1 <= len(arguments) <= MAX_ARGUMENTS or any(
        ARGUMENT_NAME.fullmatch(key) is None
        or type(item) not in {str, int, bool}
        or (isinstance(item, str) and _unsafe_text(item))
        for key, item in arguments.items()
    ):
        raise _HardIssue("RUBRIC_INVALID")
    return arguments


def _archive_paths(arguments: dict[str, object]) -> list[str]:
    """Return archive-relative path values; any `inputs/` value counts, even a logical name."""
    return [
        item
        for item in arguments.values()
        if isinstance(item, str) and item.startswith(ARCHIVE_INPUT_PREFIX)
    ]


def _schema_nodes(root: dict[str, Any], nodes: list[object]) -> list[dict[str, Any]]:
    """Expand local references and composition branches of JSON Schema nodes."""
    pending = list(nodes)
    expanded: list[dict[str, Any]] = []
    seen: set[int] = set()
    while pending:
        node = pending.pop()
        if not isinstance(node, dict):
            continue
        row = cast(dict[str, Any], node)
        if id(row) in seen:
            continue
        seen.add(id(row))
        expanded.append(row)
        reference = row.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/"):
            target: object = root
            for token in reference[2:].split("/"):
                parent = cast(dict[str, object], target) if isinstance(target, dict) else {}
                target = parent.get(token)
            pending.append(target)
        for keyword in SCHEMA_BRANCHES:
            branch: object = row.get(keyword)
            if isinstance(branch, list):
                pending.extend(cast(list[object], branch))
            elif isinstance(branch, dict):
                pending.append(cast(dict[str, object], branch))
    return expanded


def _declares_pointer(schema: dict[str, Any], pointer: str) -> bool:
    """Whether a JSON pointer names a member declared by a tool output schema."""
    nodes: list[object] = [schema]
    for raw_token in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw_token):
            return False
        token = raw_token.replace("~1", "/").replace("~0", "~")
        following: list[object] = []
        for node in _schema_nodes(schema, nodes):
            properties: object = node.get("properties")
            if isinstance(properties, dict):
                declared = cast(dict[str, object], properties)
                if token in declared:
                    following.append(declared[token])
            items: object = node.get("items")
            if isinstance(items, dict) and re.fullmatch(r"0|[1-9][0-9]*", token):
                following.append(cast(dict[str, object], items))
        if not following:
            return False
        nodes = following
    return True


def _inside_repository(path: Path) -> bool:
    """Compare directory identity, not spelling, so case or alias variants still match."""
    resolved = path.resolve()
    for directory in (resolved, *resolved.parents):
        try:
            if os.path.samefile(directory, ROOT):
                return True
        except OSError:
            continue
    return False


def _rubric(  # noqa: C901 - one closed rubric contract checked against the frozen catalog
    path: Path,
) -> tuple[dict[str, Any], bytes, dict[str, _RubricCase]]:
    """Read the external pilot rubric, the only source of cases and fact targets."""
    if _inside_repository(path):
        raise _HardIssue("RUBRIC_INSIDE_REPOSITORY")
    raw = _read_path(path, MAX_RUBRIC_BYTES, "RUBRIC")
    document = _strict_document(raw, "RUBRIC_INVALID")
    if (
        set(document)
        != {
            "schema",
            "status",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "cases",
        }
        or document["schema"] != RUBRIC_SCHEMA
        or document["status"] != "pilot_only"
        or document["arm"] != "current_22"
    ):
        raise _HardIssue("RUBRIC_INVALID")
    _identity(
        document,
        ("suite_digest", "catalog_digest", "input_ledger_digest"),
        "RUBRIC_INVALID",
    )
    values = document["cases"]
    value_rows = cast(list[object], values) if isinstance(values, list) else []
    if not isinstance(values, list) or len(value_rows) != CASE_COUNT:
        raise _HardIssue("RUBRIC_INVALID")
    tools = evaluation.load_catalogs()
    cases: dict[str, _RubricCase] = {}
    target_ids: set[str] = set()
    for value in value_rows:
        row = _object(value, "RUBRIC_INVALID")
        if set(row) != {"case_id", "expected_arguments", "targets"}:
            raise _HardIssue("RUBRIC_INVALID")
        case_id = row["case_id"]
        raw_targets = row["targets"]
        target_rows = cast(list[object], raw_targets) if isinstance(raw_targets, list) else []
        if (
            type(case_id) is not str
            or evaluation.CASE_ID.fullmatch(case_id) is None
            or case_id in cases
            or not target_rows
        ):
            raise _HardIssue("RUBRIC_INVALID")
        targets = tuple(_target(item) for item in target_rows)
        # One case binds one tool call, so every target reads the same tool result.
        if (
            len({target.json_pointer for target in targets}) != len(targets)
            or len({target.target_id for target in targets}) != len(targets)
            or len({(target.tool, target.result_schema) for target in targets}) != 1
            or any(target.target_id in target_ids for target in targets)
        ):
            raise _HardIssue("RUBRIC_INVALID")
        for target in targets:
            listed = [row for row in tools["current_22"] if row.get("name") == target.tool]
            snapshot = listed[0].get("outputSchema") if len(listed) == 1 else None
            if (
                not isinstance(snapshot, dict)
                or cast(dict[str, object], snapshot).get("$id")
                != CONTRACT_ID_PREFIX + target.result_schema
            ):
                raise _HardIssue("RUBRIC_INVALID")
            # The rubric named the tool's own contract; a mismatch now is installation drift.
            schema = cast(dict[str, Any], _catalog_schema(tools, target).schema)
            if not _declares_pointer(schema, target.json_pointer):
                raise _HardIssue("RUBRIC_INVALID")
        target_ids.update(target.target_id for target in targets)
        cases[case_id] = _RubricCase(case_id, _arguments(row["expected_arguments"]), targets)
    if sum(len(case.targets) for case in cases.values()) != FACT_COUNT:
        raise _HardIssue("RUBRIC_INVALID")
    return document, raw, cases


def _roster(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ROSTER_BYTES, "ROSTER")
    document = _strict_document(raw, "ROSTER_INVALID")
    if (
        set(document)
        != {
            "schema",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "rubric_sha256",
            "entries",
        }
        or document["schema"] != ROSTER_SCHEMA
        or document["arm"] != "current_22"
    ):
        raise _HardIssue("ROSTER_INVALID")
    _identity(
        document,
        (
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "rubric_sha256",
        ),
        "ROSTER_INVALID",
    )
    entries = document["entries"]
    entry_rows = cast(list[object], entries) if isinstance(entries, list) else []
    if not isinstance(entries, list) or len(entry_rows) != CASE_COUNT:
        raise _HardIssue("ROSTER_INVALID")
    return document, raw


def _load_evaluation(
    suite_path: Path, archive_root: Path, rubric_cases: dict[str, _RubricCase]
) -> tuple[
    dict[str, list[dict[str, object]]],
    dict[str, evaluation.Case],
    dict[str, object],
]:
    tools = evaluation.load_catalogs()
    inputs, ledger_digest = evaluation.read_ledger(archive_root)
    cases = evaluation.load_suite(suite_path, tools, inputs)
    template = evaluation.trial_template(cases, "current_22", ledger_digest)
    by_id = {case.case_id: case for case in cases if case.case_id in rubric_cases}
    if set(by_id) != set(rubric_cases):
        raise _HardIssue("PILOT_CASE_MISSING")
    for case_id, rubric_case in rubric_cases.items():
        case = by_id[case_id]
        # The suite validator gives each named first tool a one-call budget; safety cases none.
        if case.first_tool != rubric_case.targets[0].tool or any(
            path not in case.input_paths
            and not any(item.startswith(path + "/") for item in case.input_paths)
            for path in _archive_paths(rubric_case.expected_arguments)
        ):
            raise _HardIssue("RUBRIC_SUITE_MISMATCH")
    return tools, by_id, template


def _expand_arguments(arguments: dict[str, object], archive_root: Path) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in arguments.items():
        if isinstance(value, str) and value.startswith(ARCHIVE_INPUT_PREFIX):
            result[key] = str(archive_root / value)
        else:
            result[key] = value
    return result


def _catalog_schema(
    tools: dict[str, list[dict[str, object]]], target: _Target
) -> Draft202012Validator:
    matches = [row for row in tools["current_22"] if row.get("name") == target.tool]
    if len(matches) != 1 or "outputSchema" not in matches[0]:
        raise _HardIssue("CATALOG_TOOL_INVALID")
    snapshot = matches[0]["outputSchema"]
    try:
        validator = packaged_validator(target.result_schema)
        deployed = validator.schema
        if canonical_bytes(snapshot) != canonical_bytes(deployed):
            raise _HardIssue("OUTPUT_SCHEMA_MISMATCH")
        return validator
    except (SchemaConfigurationError, CanonicalizationError) as exc:
        if isinstance(exc, _HardIssue):
            raise
        raise _HardIssue("OUTPUT_SCHEMA_INVALID") from exc


def _pointer(value: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise _HardIssue("JSON_POINTER_INVALID")
    current = value
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if re.search(r"~(?![01])", raw_token):
            raise _HardIssue("JSON_POINTER_INVALID")
        if type(current) is dict:
            row = cast(dict[str, object], current)
            if token not in row:
                raise _HardIssue("JSON_POINTER_MISSING")
            current = row[token]
        elif isinstance(current, list):
            items = cast(list[object], current)
            if not re.fullmatch(r"0|[1-9][0-9]*", token):
                raise _HardIssue("JSON_POINTER_INVALID")
            index = int(token)
            if index >= len(items):
                raise _HardIssue("JSON_POINTER_MISSING")
            current = items[index]
        else:
            raise _HardIssue("JSON_POINTER_MISSING")
    return current


def _structured_results(materials: execution.VerifiedMaterials) -> tuple[bytes | None, ...]:
    return materials.structured_results


def _extract_facts(  # noqa: C901 - call, schema, and pointer binding are one boundary
    rubric_case: _RubricCase,
    report: dict[str, Any],
    materials: execution.VerifiedMaterials,
    tools: dict[str, list[dict[str, object]]],
    archive_root: Path,
) -> tuple[_PreparedFact, ...]:
    wire = report.get("wire_calls")
    wire_rows = cast(list[object], wire) if isinstance(wire, list) else []
    if not isinstance(wire, list) or len(wire_rows) != 1 or len(materials.calls) != 1:
        raise _HardIssue("CALL_COUNT_INVALID")
    call = _object(wire_rows[0], "VERIFIER_REPORT_INVALID")
    expected_arguments = _expand_arguments(rubric_case.expected_arguments, archive_root)
    target = rubric_case.targets[0]
    if (
        call.get("ordinal") != 0
        or call.get("name") != target.tool
        or call.get("arguments_digest") != digest_bytes(canonical_bytes(expected_arguments))
        or call.get("is_error") is not False
        or call.get("result_schema") != target.result_schema
        or call.get("exposed") is not True
    ):
        raise _HardIssue("CALL_BINDING_MISMATCH")
    structured_results = _structured_results(materials)
    if len(structured_results) != 1 or structured_results[0] is None:
        raise _HardIssue("STRUCTURED_RESULT_MISSING")
    structured_raw = structured_results[0]
    assert structured_raw is not None
    try:
        structured = decode_json(structured_raw, 32)
        if canonical_bytes(structured) != structured_raw:
            raise _HardIssue("STRUCTURED_RESULT_NOT_CANONICAL")
    except (DecodeError, CanonicalizationError) as exc:
        if isinstance(exc, _HardIssue):
            raise
        raise _HardIssue("STRUCTURED_RESULT_INVALID") from exc
    validators = {item.result_schema: _catalog_schema(tools, item) for item in rubric_case.targets}
    try:
        validators[target.result_schema].validate(structured)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError as exc:
        raise _HardIssue("STRUCTURED_RESULT_SCHEMA_INVALID") from exc
    structured_digest = digest_bytes(structured_raw)
    facts: list[_PreparedFact] = []
    used: set[str] = set()
    for item in rubric_case.targets:
        value = _pointer(structured, item.json_pointer)
        if item.comparison == "exact_scalar" and isinstance(value, (dict, list)):
            raise _HardIssue("FACT_COMPARISON_INVALID")
        try:
            value_raw = canonical_bytes(value)
        except CanonicalizationError as exc:
            raise _HardIssue("FACT_VALUE_INVALID") from exc
        blind_id = "bf_" + secrets.token_hex(16)
        while blind_id in used:
            blind_id = "bf_" + secrets.token_hex(16)
        used.add(blind_id)
        facts.append(_PreparedFact(item, blind_id, value_raw, structured_digest))
    return tuple(facts)


def _base_report(operation: Literal["prepare", "assess"]) -> dict[str, Any]:
    return {
        "schema": REPORT_SCHEMA,
        "operation": operation,
        "fact_binding_state": "not_adjudicable",
        "annotation_state": "not_adjudicable",
        "population": {
            "cases_expected": CASE_COUNT,
            "facts_expected": FACT_COUNT,
            "cases_bound": 0,
            "facts_bound": 0,
            "facts_double_annotated": 0,
            "facts_resolved": 0,
        },
        "inputs": {},
        "cases": [],
        "facts": None,
        "reasons": [],
        "mapping": None,
        "limitations": LIMITATIONS,
    }


def _case_report(case_id: str, bundle_sha256: str | None = None) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "binding_state": "not_adjudicable",
        "annotation_state": "not_adjudicable",
        "fact_count": 0,
        "bundle_sha256": bundle_sha256,
        "verifier_report_sha256": None,
        "reasons": [],
    }


def _finish(output: dict[str, Any]) -> dict[str, Any]:
    cases = cast(list[dict[str, Any]], output["cases"])
    internal_reasons = {
        reason for row in cases for reason in cast(list[str], row.get("reasons", []))
    }
    output["reasons"] = sorted(set(cast(list[str], output["reasons"])) | internal_reasons)
    output["cases"] = [
        {
            "case_id": row["case_id"],
            "fact_binding_state": row["binding_state"],
            "annotation_state": row["annotation_state"],
            "fact_count": row["fact_count"],
        }
        for row in cases
    ]
    return output


def _soft(row: dict[str, Any], reason: str) -> None:
    reasons = cast(list[str], row["reasons"])
    if reason not in reasons:
        reasons.append(reason)


def _verifier_failure(row: dict[str, Any], report: dict[str, Any]) -> None:
    reasons = report.get("reasons")
    reason_rows = cast(list[object], reasons) if isinstance(reasons, list) else []
    if reason_rows and all(type(value) is str for value in reason_rows):
        for reason in cast(list[str], reason_rows):
            _soft(row, f"VERIFIER_{reason}")
    else:
        _soft(row, "VERIFIER_NOT_ADJUDICABLE")


def _packet(
    blind_trial_id: str,
    case: evaluation.Case,
    report: dict[str, Any],
    materials: execution.VerifiedMaterials,
    protocol_sha256: str,
    facts: tuple[_PreparedFact, ...],
) -> dict[str, Any]:
    try:
        base = blind._packet(blind_trial_id, case, report, materials, protocol_sha256)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc
    base["schema"] = PACKET_SCHEMA
    base["factual_targets"] = [fact.packet_row() for fact in facts]
    return base


def _roster_digest(entries: list[_PreparedCase], rubric_sha256: str, protocol_sha256: str) -> str:
    rows = [
        {
            "blind_trial_id": entry.blind_trial_id,
            "final_answer_sha256": entry.final_answer_sha256,
            "review_packet_sha256": entry.packet.sha256,
            "blind_fact_ids": sorted(fact.blind_fact_id for fact in entry.facts),
        }
        for entry in sorted(entries, key=lambda item: item.blind_trial_id)
    ]
    return digest_bytes(
        canonical_bytes(
            {
                "rubric_sha256": rubric_sha256,
                "annotation_protocol_sha256": protocol_sha256,
                "entries": rows,
            }
        )
    )


def prepare(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917
    roster_path: Path,
    bundle_root: Path,
    suite_path: Path,
    archive_root: Path,
    rubric_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Verify three executions and emit blinded factual packets plus mapping."""
    output = _base_report("prepare")
    rubric, rubric_raw, rubric_cases = _rubric(rubric_path)
    roster, roster_raw = _roster(roster_path)
    rubric_sha256 = _sha256(rubric_raw)
    output["inputs"] = {
        "roster_sha256": _sha256(roster_raw),
        "rubric_sha256": rubric_sha256,
    }
    tools, cases, template = _load_evaluation(suite_path, archive_root, rubric_cases)
    identity = ("suite_digest", "catalog_digest", "input_ledger_digest")
    global_reasons: list[str] = []
    if roster["rubric_sha256"] != rubric_sha256 or any(
        roster[key] != template[key] or rubric[key] != template[key] for key in identity
    ):
        global_reasons.append("IDENTITY_MISMATCH")

    by_case: dict[str, tuple[blind._Descriptor, dict[str, Any]]] = {}
    for value in cast(list[object], roster["entries"]):
        row = _object(value, "ROSTER_INVALID")
        if set(row) != {"case_id", "bundle"}:
            raise _HardIssue("ROSTER_INVALID")
        case_id = row["case_id"]
        descriptor = _descriptor(
            row["bundle"], MAX_BUNDLE_MANIFEST_BYTES, "BUNDLE_DESCRIPTOR_INVALID"
        )
        report = _case_report(cast(str, case_id), descriptor.sha256)
        cast(list[dict[str, Any]], output["cases"]).append(report)
        if type(case_id) is not str or case_id not in rubric_cases:
            _soft(report, "UNEXPECTED_CASE")
            global_reasons.append("ROSTER_CASE_INVALID")
        elif case_id in by_case:
            _soft(report, "DUPLICATE_CASE")
            global_reasons.append("DUPLICATE_CASE")
        else:
            by_case[case_id] = descriptor, report
    if set(by_case) != set(rubric_cases):
        global_reasons.append("PILOT_INCOMPLETE")

    protocol_raw = _read_path(ANNOTATION_PROTOCOL, MAX_PROTOCOL_BYTES, "ANNOTATION_PROTOCOL")
    protocol_sha256 = _sha256(protocol_raw)
    prepared: list[tuple[_PreparedCase, bytes]] = []
    total_bundle_bytes = 0
    used_trial_ids: set[str] = set()
    used_fact_ids: set[str] = set()
    for case_id, (descriptor, case_report) in by_case.items():
        if global_reasons:
            _soft(case_report, "GLOBAL_IDENTITY_INVALID")
            continue
        bundle_raw, reason = _read_descriptor(
            bundle_root, descriptor, MAX_BUNDLE_MANIFEST_BYTES, missing_is_soft=True
        )
        if reason is not None:
            _soft(case_report, reason)
            continue
        assert bundle_raw is not None
        verifier_report, materials = execution.verify_materials(
            bundle_root / descriptor.path, suite_path, archive_root
        )
        total_bundle_bytes += len(bundle_raw) + (
            materials.artifact_bytes if materials is not None else FAILED_BUNDLE_ARTIFACT_BOUND
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
            or verifier_report.get("case_id") != case_id
            or verifier_report.get("arm") != "current_22"
            or any(verifier_report.get(key) != template[key] for key in identity)
        ):
            _soft(case_report, "VERIFIER_IDENTITY_MISMATCH")
            continue
        try:
            facts = _extract_facts(
                rubric_cases[case_id], verifier_report, materials, tools, archive_root
            )
        except _HardIssue as exc:
            _soft(case_report, str(exc))
            continue
        if any(fact.blind_fact_id in used_fact_ids for fact in facts):
            raise _HardIssue("BLIND_ID_COLLISION")
        used_fact_ids.update(fact.blind_fact_id for fact in facts)
        blind_trial_id = "bt_" + secrets.token_hex(16)
        while blind_trial_id in used_trial_ids:
            blind_trial_id = "bt_" + secrets.token_hex(16)
        used_trial_ids.add(blind_trial_id)
        packet = _packet(
            blind_trial_id,
            cases[case_id],
            verifier_report,
            materials,
            protocol_sha256,
            facts,
        )
        packet_raw = _canonical_file(packet)
        if len(packet_raw) > MAX_PACKET_BYTES:
            raise _HardIssue("PACKET_LIMIT_EXCEEDED")
        packet_descriptor = blind._Descriptor(
            f"{blind_trial_id}.json", len(packet_raw), _sha256(packet_raw)
        )
        wire = cast(list[dict[str, Any]], verifier_report["wire_calls"])[0]
        entry = _PreparedCase(
            case_id,
            blind_trial_id,
            descriptor,
            packet_descriptor,
            verifier_digest,
            cast(str, wire["request_raw_sha256"]),
            cast(str, wire["response_raw_sha256"]),
            _sha256(materials.final_answer),
            facts,
        )
        prepared.append((entry, packet_raw))
        case_report["binding_state"] = "bound"
        case_report["fact_count"] = len(facts)
        output["population"]["cases_bound"] += 1
        output["population"]["facts_bound"] += len(facts)

    case_rows = cast(list[dict[str, Any]], output["cases"])
    if (
        global_reasons
        or len(prepared) != CASE_COUNT
        or sum(len(entry.facts) for entry, _raw in prepared) != FACT_COUNT
        or any(row["binding_state"] != "bound" for row in case_rows)
    ):
        output["reasons"] = sorted(
            set(global_reasons)
            | {reason for row in case_rows for reason in cast(list[str], row["reasons"])}
        )
        return _finish(output)
    if sum(len(raw) for _entry, raw in prepared) > MAX_TOTAL_PACKET_BYTES:
        raise _HardIssue("PACKET_TOTAL_LIMIT_EXCEEDED")

    output_fd = blind._new_output_directory(output_dir)
    try:
        blind._write_private(output_fd, ANNOTATION_PROTOCOL.name, protocol_raw)
        for entry, packet_raw in prepared:
            blind._write_private(output_fd, entry.packet.path, packet_raw)
        entries = [entry for entry, _raw in prepared]
        mapping = {
            "schema": MAPPING_SCHEMA,
            "arm": "current_22",
            "suite_digest": template["suite_digest"],
            "catalog_digest": template["catalog_digest"],
            "input_ledger_digest": template["input_ledger_digest"],
            "rubric_sha256": rubric_sha256,
            "annotation_protocol_sha256": protocol_sha256,
            "annotation_protocol": {
                "path": ANNOTATION_PROTOCOL.name,
                "bytes": len(protocol_raw),
                "sha256": protocol_sha256,
            },
            "blind_roster_digest": _roster_digest(entries, rubric_sha256, protocol_sha256),
            "entries": [entry.mapping_row() for entry in entries],
        }
        mapping_raw = _canonical_file(mapping)
        if len(mapping_raw) > MAX_MAPPING_BYTES:
            raise _HardIssue("MAPPING_LIMIT_EXCEEDED")
        mapping_path = output_dir / "factual-blind-mapping.v1.json"
        blind._write_private(output_fd, mapping_path.name, mapping_raw)
        blind._same_output_directory(output_dir, output_fd)
    except blind._HardIssue as exc:
        raise _HardIssue(str(exc)) from exc
    finally:
        os.close(output_fd)
    output["fact_binding_state"] = "bound"
    output["mapping"] = str(mapping_path)
    output["inputs"]["mapping_sha256"] = _sha256(mapping_raw)
    return _finish(output)


def _mapping(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_MAPPING_BYTES, "MAPPING")
    document = _strict_document(raw, "MAPPING_INVALID")
    if (
        set(document)
        != {
            "schema",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "rubric_sha256",
            "annotation_protocol_sha256",
            "annotation_protocol",
            "blind_roster_digest",
            "entries",
        }
        or document["schema"] != MAPPING_SCHEMA
        or document["arm"] != "current_22"
    ):
        raise _HardIssue("MAPPING_INVALID")
    _identity(
        document,
        (
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "rubric_sha256",
            "annotation_protocol_sha256",
            "blind_roster_digest",
        ),
        "MAPPING_INVALID",
    )
    _local_descriptor(
        document["annotation_protocol"], MAX_PROTOCOL_BYTES, "PROTOCOL_DESCRIPTOR_INVALID"
    )
    entries = document["entries"]
    entry_rows = cast(list[object], entries) if isinstance(entries, list) else []
    if not isinstance(entries, list) or len(entry_rows) != CASE_COUNT:
        raise _HardIssue("MAPPING_INVALID")
    return document, raw


def _mapping_fact(value: object, target: _Target) -> _PreparedFact:
    row = _object(value, "MAPPING_INVALID")
    if set(row) != {
        "target_id",
        "blind_fact_id",
        "call_ordinal",
        "structured_content_sha256",
        "canonical_value_sha256",
    }:
        raise _HardIssue("MAPPING_INVALID")
    if (
        row["target_id"] != target.target_id
        or type(row["blind_fact_id"]) is not str
        or BLIND_FACT_ID.fullmatch(row["blind_fact_id"]) is None
        or row["call_ordinal"] != target.call_ordinal
    ):
        raise _HardIssue("MAPPING_INVALID")
    _identity(row, ("structured_content_sha256", "canonical_value_sha256"), "MAPPING_INVALID")
    return _PreparedFact(
        target,
        row["blind_fact_id"],
        b"",
        row["structured_content_sha256"],
    )


def _mapping_entry(value: object, rubric_case: _RubricCase) -> _PreparedCase:
    row = _object(value, "MAPPING_INVALID")
    if set(row) != {
        "case_id",
        "blind_trial_id",
        "bundle",
        "packet",
        "verifier_report_sha256",
        "request_raw_sha256",
        "response_raw_sha256",
        "final_answer_sha256",
        "facts",
    }:
        raise _HardIssue("MAPPING_INVALID")
    raw_facts = row["facts"]
    fact_rows = cast(list[object], raw_facts) if isinstance(raw_facts, list) else []
    if (
        row["case_id"] != rubric_case.case_id
        or type(row["blind_trial_id"]) is not str
        or BLIND_TRIAL_ID.fullmatch(row["blind_trial_id"]) is None
        or not isinstance(raw_facts, list)
        or len(fact_rows) != len(rubric_case.targets)
    ):
        raise _HardIssue("MAPPING_INVALID")
    _identity(
        row,
        (
            "verifier_report_sha256",
            "request_raw_sha256",
            "response_raw_sha256",
            "final_answer_sha256",
        ),
        "MAPPING_INVALID",
    )
    facts_by_target: dict[str, _PreparedFact] = {}
    for raw_fact in fact_rows:
        fact_row = _object(raw_fact, "MAPPING_INVALID")
        target_id = fact_row.get("target_id")
        targets = [target for target in rubric_case.targets if target.target_id == target_id]
        if len(targets) != 1 or cast(str, target_id) in facts_by_target:
            raise _HardIssue("MAPPING_INVALID")
        facts_by_target[cast(str, target_id)] = _mapping_fact(raw_fact, targets[0])
    return _PreparedCase(
        rubric_case.case_id,
        row["blind_trial_id"],
        _descriptor(row["bundle"], MAX_BUNDLE_MANIFEST_BYTES, "BUNDLE_DESCRIPTOR_INVALID"),
        _local_descriptor(row["packet"], MAX_PACKET_BYTES, "PACKET_DESCRIPTOR_INVALID"),
        row["verifier_report_sha256"],
        row["request_raw_sha256"],
        row["response_raw_sha256"],
        row["final_answer_sha256"],
        tuple(facts_by_target[target.target_id] for target in rubric_case.targets),
    )


def _annotation_span(value: object) -> tuple[int, int]:
    row = _object(value, "ANNOTATION_SPAN_INVALID")
    if set(row) != {"start", "end"}:
        raise _HardIssue("ANNOTATION_SPAN_INVALID")
    start, end = row["start"], row["end"]
    if type(start) is not int or type(end) is not int:
        raise _HardIssue("ANNOTATION_SPAN_INVALID")
    return start, end


def _annotation_row(
    value: object,
) -> tuple[str, str, str, str, str, _Annotation]:
    row = _object(value, "ANNOTATIONS_INVALID")
    base = {
        "blind_trial_id",
        "blind_fact_id",
        "final_answer_sha256",
        "review_packet_sha256",
        "relation",
    }
    if set(row) not in (base, base | {"answer_span"}):
        raise _HardIssue("ANNOTATIONS_INVALID")
    trial_id, fact_id, relation = row["blind_trial_id"], row["blind_fact_id"], row["relation"]
    if (
        type(trial_id) is not str
        or BLIND_TRIAL_ID.fullmatch(trial_id) is None
        or type(fact_id) is not str
        or BLIND_FACT_ID.fullmatch(fact_id) is None
        or type(relation) is not str
        or relation not in RELATIONS
    ):
        raise _HardIssue("ANNOTATIONS_INVALID")
    _identity(row, ("final_answer_sha256", "review_packet_sha256"), "ANNOTATIONS_INVALID")
    span = _annotation_span(row["answer_span"]) if "answer_span" in row else None
    return (
        trial_id,
        fact_id,
        row["final_answer_sha256"],
        row["review_packet_sha256"],
        relation,
        _Annotation(relation, span),
    )


def _annotations(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ANNOTATION_BYTES, "ANNOTATIONS")
    document = _strict_document(raw, "ANNOTATIONS_INVALID")
    raw_annotations = document.get("annotations")
    annotation_rows = (
        cast(list[object], raw_annotations) if isinstance(raw_annotations, list) else []
    )
    if (
        set(document)
        != {
            "schema",
            "blind_roster_digest",
            "rubric_sha256",
            "annotation_protocol_sha256",
            "reviewer_id",
            "annotations",
        }
        or document["schema"] != ANNOTATIONS_SCHEMA
        or type(document["reviewer_id"]) is not str
        or REVIEWER_ID.fullmatch(document["reviewer_id"]) is None
        or not isinstance(raw_annotations, list)
        or len(annotation_rows) != FACT_COUNT
    ):
        raise _HardIssue("ANNOTATIONS_INVALID")
    _identity(
        document,
        ("blind_roster_digest", "rubric_sha256", "annotation_protocol_sha256"),
        "ANNOTATIONS_INVALID",
    )
    return document, raw


def _span_matches(final_answer: bytes, span: tuple[int, int], canonical_value: bytes) -> bool:
    start, end = span
    if not 0 <= start < end <= len(final_answer):
        return False
    try:
        value = decode_json(final_answer[start:end], 32)
        return canonical_bytes(value) == canonical_value
    except (DecodeError, CanonicalizationError, RecursionError):
        return False


def _validate_relation(
    annotation: _Annotation,
    fact: _PreparedFact,
    final_answer: bytes,
) -> tuple[bool, bool | None]:
    if fact.target.comparison == "exact_scalar":
        return annotation.relation != "partial" and annotation.answer_span is None, None
    if annotation.relation == "supported":
        if annotation.answer_span is None:
            return False, None
        matched = _span_matches(final_answer, annotation.answer_span, fact.canonical_value)
        return matched, matched
    return annotation.answer_span is None, None


def _index_annotations(
    document: dict[str, Any],
    mapped: dict[str, tuple[_PreparedCase, _PreparedFact, bytes]],
) -> tuple[dict[str, _Annotation], list[str]]:
    result: dict[str, _Annotation] = {}
    reasons: list[str] = []
    for value in cast(list[object], document["annotations"]):
        trial_id, fact_id, answer, packet, _relation, annotation = _annotation_row(value)
        expected = mapped.get(fact_id)
        if fact_id in result:
            reasons.append("DUPLICATE_ANNOTATION")
            continue
        if expected is None or expected[0].blind_trial_id != trial_id:
            reasons.append("UNEXPECTED_ANNOTATION")
            continue
        entry, fact, final_answer = expected
        if answer != entry.final_answer_sha256 or packet != entry.packet.sha256:
            reasons.append("ANNOTATION_BINDING_MISMATCH")
            continue
        valid, _span_match = _validate_relation(annotation, fact, final_answer)
        if not valid:
            reasons.append("ANNOTATION_RELATION_INVALID")
            continue
        result[fact_id] = annotation
    if set(result) != set(mapped):
        reasons.append("ANNOTATION_ROSTER_INCOMPLETE")
    return result, reasons


def _rationale(value: object) -> str:
    if type(value) is not str or not 1 <= len(value) <= MAX_RATIONALE_CHARS or not value.strip():
        raise _HardIssue("ADJUDICATION_RATIONALE_INVALID")
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} and char not in "\n "
        for char in value
    ):
        raise _HardIssue("ADJUDICATION_RATIONALE_INVALID")
    return value


def _adjudication(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = _read_path(path, MAX_ADJUDICATION_BYTES, "ADJUDICATION")
    document = _strict_document(raw, "ADJUDICATION_INVALID")
    if (
        set(document)
        != {
            "schema",
            "blind_roster_digest",
            "rubric_sha256",
            "annotation_protocol_sha256",
            "annotation_sha256",
            "adjudicator_id",
            "resolutions",
        }
        or document["schema"] != ADJUDICATION_SCHEMA
        or type(document["adjudicator_id"]) is not str
        or REVIEWER_ID.fullmatch(document["adjudicator_id"]) is None
        or not isinstance(document["annotation_sha256"], list)
        or len(cast(list[object], document["annotation_sha256"])) != PAIRED_REVIEWER_COUNT
        or any(
            type(value) is not str or evaluation.DIGEST.fullmatch(value) is None
            for value in cast(list[object], document["annotation_sha256"])
        )
        or cast(list[str], document["annotation_sha256"])
        != sorted(cast(list[str], document["annotation_sha256"]))
        or len(set(cast(list[str], document["annotation_sha256"]))) != PAIRED_REVIEWER_COUNT
        or not isinstance(document["resolutions"], list)
    ):
        raise _HardIssue("ADJUDICATION_INVALID")
    _identity(
        document,
        ("blind_roster_digest", "rubric_sha256", "annotation_protocol_sha256"),
        "ADJUDICATION_INVALID",
    )
    return document, raw


def _resolution_row(value: object) -> tuple[str, str, str, str, _Annotation]:
    row = _object(value, "ADJUDICATION_INVALID")
    base = {
        "blind_trial_id",
        "blind_fact_id",
        "final_answer_sha256",
        "review_packet_sha256",
        "relation",
        "rationale",
    }
    if set(row) not in (base, base | {"answer_span"}):
        raise _HardIssue("ADJUDICATION_INVALID")
    _rationale(row["rationale"])
    annotation_row = {key: item for key, item in row.items() if key != "rationale"}
    trial, fact, answer, packet, _relation, annotation = _annotation_row(annotation_row)
    return trial, fact, answer, packet, annotation


def _resolve(  # noqa: C901 - closed third-reviewer adjudication contract
    adjudication_path: Path | None,
    disagreements: set[str],
    mapped: dict[str, tuple[_PreparedCase, _PreparedFact, bytes]],
    annotation_digests: list[str],
    reviewer_ids: set[str],
    mapping: dict[str, Any],
) -> tuple[dict[str, _Annotation], list[str], str | None]:
    if not disagreements:
        return {}, (["UNNEEDED_ADJUDICATION"] if adjudication_path is not None else []), None
    if adjudication_path is None:
        return {}, ["ADJUDICATION_MISSING"], None
    document, raw = _adjudication(adjudication_path)
    reasons: list[str] = []
    if (
        document["blind_roster_digest"] != mapping["blind_roster_digest"]
        or document["rubric_sha256"] != mapping["rubric_sha256"]
        or document["annotation_protocol_sha256"] != mapping["annotation_protocol_sha256"]
        or document["annotation_sha256"] != annotation_digests
    ):
        reasons.append("ADJUDICATION_BINDING_MISMATCH")
    if document["adjudicator_id"] in reviewer_ids:
        reasons.append("ADJUDICATOR_NOT_DISTINCT")
    resolutions: dict[str, _Annotation] = {}
    rows = cast(list[object], document["resolutions"])
    if len(rows) != len(disagreements):
        reasons.append("ADJUDICATION_INCOMPLETE")
    for value in rows:
        trial_id, fact_id, answer, packet, annotation = _resolution_row(value)
        expected = mapped.get(fact_id)
        if fact_id in resolutions:
            reasons.append("DUPLICATE_RESOLUTION")
            continue
        if (
            fact_id not in disagreements
            or expected is None
            or expected[0].blind_trial_id != trial_id
        ):
            reasons.append("UNEXPECTED_RESOLUTION")
            continue
        entry, fact, final_answer = expected
        valid, _span_match = _validate_relation(annotation, fact, final_answer)
        if answer != entry.final_answer_sha256 or packet != entry.packet.sha256 or not valid:
            reasons.append("ADJUDICATION_BINDING_MISMATCH")
            continue
        resolutions[fact_id] = annotation
    if set(resolutions) != disagreements:
        reasons.append("ADJUDICATION_INCOMPLETE")
    return resolutions, reasons, _sha256(raw)


def assess(  # noqa: C901, PLR0912, PLR0913, PLR0915, PLR0917
    mapping_path: Path,
    bundle_root: Path,
    annotation_a: Path,
    annotation_b: Path,
    adjudication_path: Path | None,
    suite_path: Path,
    archive_root: Path,
    rubric_path: Path,
) -> dict[str, Any]:
    """Reverify factual packets and resolve complete double annotation."""
    output = _base_report("assess")
    mapping, mapping_raw = _mapping(mapping_path)
    rubric, rubric_raw, rubric_cases = _rubric(rubric_path)
    rubric_sha256 = _sha256(rubric_raw)
    output["inputs"] = {
        "mapping_sha256": _sha256(mapping_raw),
        "rubric_sha256": rubric_sha256,
    }
    tools, cases, template = _load_evaluation(suite_path, archive_root, rubric_cases)
    identity = ("suite_digest", "catalog_digest", "input_ledger_digest")
    reasons: list[str] = []
    if mapping["rubric_sha256"] != rubric_sha256 or any(
        mapping[key] != template[key] or rubric[key] != template[key] for key in identity
    ):
        reasons.append("IDENTITY_MISMATCH")

    protocol_descriptor = _local_descriptor(
        mapping["annotation_protocol"], MAX_PROTOCOL_BYTES, "PROTOCOL_DESCRIPTOR_INVALID"
    )
    protocol_raw, protocol_reason = _read_descriptor(
        mapping_path.parent, protocol_descriptor, MAX_PROTOCOL_BYTES, missing_is_soft=True
    )
    if protocol_reason is not None:
        reasons.append(protocol_reason)
    elif protocol_descriptor.sha256 != mapping[
        "annotation_protocol_sha256"
    ] or protocol_raw != _read_path(ANNOTATION_PROTOCOL, MAX_PROTOCOL_BYTES, "ANNOTATION_PROTOCOL"):
        reasons.append("ANNOTATION_PROTOCOL_MISMATCH")

    entries: list[_PreparedCase] = []
    by_case: dict[str, _PreparedCase] = {}
    by_trial: set[str] = set()
    fact_ids: set[str] = set()
    for value in cast(list[object], mapping["entries"]):
        row = _object(value, "MAPPING_INVALID")
        case_id = row.get("case_id")
        if type(case_id) is not str or case_id not in rubric_cases:
            raise _HardIssue("MAPPING_INVALID")
        entry = _mapping_entry(value, rubric_cases[case_id])
        if (
            case_id in by_case
            or entry.blind_trial_id in by_trial
            or any(fact.blind_fact_id in fact_ids for fact in entry.facts)
        ):
            raise _HardIssue("MAPPING_INVALID")
        by_case[case_id] = entry
        by_trial.add(entry.blind_trial_id)
        fact_ids.update(fact.blind_fact_id for fact in entry.facts)
        entries.append(entry)
        cast(list[dict[str, Any]], output["cases"]).append(
            _case_report(case_id, entry.bundle.sha256)
        )
    if set(by_case) != set(rubric_cases) or len(fact_ids) != FACT_COUNT:
        reasons.append("MAPPING_INCOMPLETE")
    if (
        _roster_digest(entries, rubric_sha256, cast(str, mapping["annotation_protocol_sha256"]))
        != mapping["blind_roster_digest"]
    ):
        reasons.append("BLIND_ROSTER_DIGEST_MISMATCH")

    case_reports = {
        cast(str, row["case_id"]): row for row in cast(list[dict[str, Any]], output["cases"])
    }
    bound: dict[str, tuple[_PreparedCase, _PreparedFact, bytes]] = {}
    total_packet_bytes = 0
    total_bundle_bytes = 0
    for case_id, entry in by_case.items():
        row = case_reports[case_id]
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
            materials.artifact_bytes if materials is not None else FAILED_BUNDLE_ARTIFACT_BOUND
        )
        if total_bundle_bytes > MAX_TOTAL_BUNDLE_BYTES:
            raise _HardIssue("BUNDLE_TOTAL_LIMIT_EXCEEDED")
        verifier_digest = digest_bytes(canonical_bytes(verifier_report))
        row["verifier_report_sha256"] = verifier_digest
        if verifier_report.get("state") != "adjudicable" or materials is None:
            _verifier_failure(row, verifier_report)
            continue
        wire = verifier_report.get("wire_calls")
        wire_rows = cast(list[object], wire) if isinstance(wire, list) else []
        if not isinstance(wire, list) or len(wire_rows) != 1:
            _soft(row, "CALL_COUNT_INVALID")
            continue
        wire_call = _object(wire_rows[0], "VERIFIER_REPORT_INVALID")
        if (
            materials.manifest_bytes != entry.bundle.bytes
            or materials.manifest_sha256 != entry.bundle.sha256
            or verifier_digest != entry.verifier_report_sha256
            or verifier_report.get("case_id") != case_id
            or verifier_report.get("arm") != "current_22"
            or _sha256(materials.final_answer) != entry.final_answer_sha256
            or wire_call.get("request_raw_sha256") != entry.request_raw_sha256
            or wire_call.get("response_raw_sha256") != entry.response_raw_sha256
        ):
            _soft(row, "VERIFIER_MATERIAL_MISMATCH")
            continue
        try:
            extracted = _extract_facts(
                rubric_cases[case_id], verifier_report, materials, tools, archive_root
            )
        except _HardIssue as exc:
            _soft(row, str(exc))
            continue
        extracted_by_target = {fact.target.target_id: fact for fact in extracted}
        rebound: list[_PreparedFact] = []
        mismatch = False
        for mapped_fact in entry.facts:
            observed = extracted_by_target[mapped_fact.target.target_id]
            mapping_row = next(
                cast(dict[str, Any], item)
                for item in cast(
                    list[object],
                    next(
                        cast(dict[str, Any], value)
                        for value in cast(list[object], mapping["entries"])
                        if cast(dict[str, Any], value).get("case_id") == case_id
                    )["facts"],
                )
                if cast(dict[str, Any], item).get("target_id") == mapped_fact.target.target_id
            )
            if (
                observed.structured_content_sha256 != mapped_fact.structured_content_sha256
                or digest_bytes(observed.canonical_value) != mapping_row["canonical_value_sha256"]
            ):
                mismatch = True
                break
            rebound.append(
                _PreparedFact(
                    mapped_fact.target,
                    mapped_fact.blind_fact_id,
                    observed.canonical_value,
                    observed.structured_content_sha256,
                )
            )
        if mismatch:
            _soft(row, "FACT_BINDING_MISMATCH")
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
        expected_packet = _packet(
            entry.blind_trial_id,
            cases[case_id],
            verifier_report,
            materials,
            cast(str, mapping["annotation_protocol_sha256"]),
            tuple(rebound),
        )
        if canonical_bytes(_strict_document(packet_raw, "PACKET_INVALID")) != canonical_bytes(
            expected_packet
        ):
            _soft(row, "PACKET_MISMATCH")
            continue
        row["binding_state"] = "bound"
        row["fact_count"] = len(rebound)
        output["population"]["cases_bound"] += 1
        output["population"]["facts_bound"] += len(rebound)
        rebound_entry = _PreparedCase(
            entry.case_id,
            entry.blind_trial_id,
            entry.bundle,
            entry.packet,
            entry.verifier_report_sha256,
            entry.request_raw_sha256,
            entry.response_raw_sha256,
            entry.final_answer_sha256,
            tuple(rebound),
        )
        for fact in rebound:
            bound[fact.blind_fact_id] = rebound_entry, fact, materials.final_answer

    if (
        reasons
        or len(bound) != FACT_COUNT
        or any(
            row["binding_state"] != "bound" for row in cast(list[dict[str, Any]], output["cases"])
        )
    ):
        output["reasons"] = sorted(
            set(reasons)
            | {
                reason
                for row in cast(list[dict[str, Any]], output["cases"])
                for reason in cast(list[str], row["reasons"])
            }
        )
        return _finish(output)
    output["fact_binding_state"] = "bound"

    document_a, raw_a = _annotations(annotation_a)
    document_b, raw_b = _annotations(annotation_b)
    digest_a, digest_b = _sha256(raw_a), _sha256(raw_b)
    output["inputs"].update(annotation_a_sha256=digest_a, annotation_b_sha256=digest_b)
    if digest_a == digest_b:
        reasons.append("ANNOTATION_FILES_NOT_DISTINCT")
    reviewer_a, reviewer_b = document_a["reviewer_id"], document_b["reviewer_id"]
    if reviewer_a == reviewer_b:
        reasons.append("REVIEWERS_NOT_DISTINCT")
    for document in (document_a, document_b):
        if (
            document["blind_roster_digest"] != mapping["blind_roster_digest"]
            or document["rubric_sha256"] != rubric_sha256
            or document["annotation_protocol_sha256"] != mapping["annotation_protocol_sha256"]
        ):
            reasons.append("ANNOTATION_BINDING_MISMATCH")
    indexed_a, reasons_a = _index_annotations(document_a, bound)
    indexed_b, reasons_b = _index_annotations(document_b, bound)
    reasons.extend(reasons_a)
    reasons.extend(reasons_b)
    double = set(indexed_a) & set(indexed_b)
    output["population"]["facts_double_annotated"] = len(double)
    disagreements = {
        fact_id for fact_id in double if indexed_a[fact_id].relation != indexed_b[fact_id].relation
    }
    resolutions, resolution_reasons, adjudication_digest = _resolve(
        adjudication_path,
        disagreements,
        bound,
        sorted([digest_a, digest_b]),
        {cast(str, reviewer_a), cast(str, reviewer_b)},
        mapping,
    )
    reasons.extend(resolution_reasons)
    if adjudication_digest is not None:
        output["inputs"]["adjudication_sha256"] = adjudication_digest

    if reasons or len(double) != FACT_COUNT or len(resolutions) != len(disagreements):
        output["reasons"] = sorted(set(reasons))
        return _finish(output)

    fact_rows: list[dict[str, object]] = []
    for fact_id in sorted(bound):
        entry, fact, final_answer = bound[fact_id]
        if fact_id in disagreements:
            selected = resolutions[fact_id]
            basis = "third_reviewer_adjudication"
        else:
            selected = indexed_a[fact_id]
            basis = "paired_annotation_agreement"
        valid, span_match = _validate_relation(selected, fact, final_answer)
        if not valid:
            output["reasons"] = ["FINAL_RELATION_INVALID"]
            return _finish(output)
        row: dict[str, object] = {
            "case_id": entry.case_id,
            "target_id": fact.target.target_id,
            "relation": PUBLIC_RELATIONS[selected.relation],
            "relation_basis": basis,
        }
        if fact.target.comparison == "canonical_json_value" and selected.relation == "supported":
            row["span_value_match"] = span_match is True
        fact_rows.append(row)
    output["population"]["facts_resolved"] = FACT_COUNT
    output["annotation_state"] = "resolved"
    output["facts"] = fact_rows
    for row in cast(list[dict[str, Any]], output["cases"]):
        row["annotation_state"] = "resolved"
    return _finish(output)


RUBRIC_HELP = (
    "External pilot rubric naming cases, expected arguments and fact targets. "
    "It must be a regular file outside this repository; the file and its parent directory "
    "must not be symbolic links. A missing file stops with RUBRIC_MISSING."
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    prepare_parser = actions.add_parser("prepare")
    prepare_parser.add_argument("--roster", type=Path, required=True)
    prepare_parser.add_argument("--bundle-root", type=Path, required=True)
    prepare_parser.add_argument("--suite", type=Path, required=True)
    prepare_parser.add_argument("--archive-root", type=Path, required=True)
    prepare_parser.add_argument("--rubric", type=Path, required=True, help=RUBRIC_HELP)
    prepare_parser.add_argument("--output-dir", type=Path, required=True)
    assess_parser = actions.add_parser("assess")
    assess_parser.add_argument("--mapping", type=Path, required=True)
    assess_parser.add_argument("--bundle-root", type=Path, required=True)
    assess_parser.add_argument("--reviewer-a", type=Path, required=True)
    assess_parser.add_argument("--reviewer-b", type=Path, required=True)
    assess_parser.add_argument("--adjudication", type=Path)
    assess_parser.add_argument("--suite", type=Path, required=True)
    assess_parser.add_argument("--archive-root", type=Path, required=True)
    assess_parser.add_argument("--rubric", type=Path, required=True, help=RUBRIC_HELP)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.action == "prepare":
            report = prepare(
                args.roster,
                args.bundle_root,
                args.suite,
                args.archive_root,
                args.rubric,
                args.output_dir,
            )
        else:
            report = assess(
                args.mapping,
                args.bundle_root,
                args.reviewer_a,
                args.reviewer_b,
                args.adjudication,
                args.suite,
                args.archive_root,
                args.rubric,
            )
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

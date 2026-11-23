"""Check local A3 wire, model-event, and final-answer agreement without scoring.

The input bundle is caller supplied. A successful binding establishes internal
consistency of those files, not model identity, provenance, or task correctness.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

from scripts import evaluate_agent_surface_v2 as evaluation
from scripts._agent_surface_eval_fileio import read_relative
from underwrite._json import DecodeError, decode_json
from underwrite._model_json_text import has_text_controls
from underwrite.cli.evidence import EvidenceError

BUNDLE_SCHEMA = "underwrite.agent_surface_execution_bundle.v1"
REPORT_SCHEMA = "underwrite.agent_surface_execution_binding.v1"
MODEL_EVENT_FORMAT = "codex.exec-json.v1"
ARTIFACT_ROLES = ("wire", "model_events", "final_answer")
ARTIFACT_LIMITS = {
    "wire": 2 * 1024 * 1024,
    "model_events": 4 * 1024 * 1024,
    "final_answer": 64 * 1024,
}
MAX_BUNDLE_BYTES = 64 * 1024
MAX_WIRE_EVENTS = 512
MAX_MODEL_EVENTS = 512
MIN_MODEL_EVENTS = 3
MAX_RAW_MESSAGE_BYTES = 512 * 1024
MAX_JSONL_LINE_BYTES = 768 * 1024
MAX_BIDI_SOURCE_VALUES = 256
MAX_BIDI_MATCHES = 65_536
ASCII_CONTROL_END = 32
ASCII_DELETE = 127
ERROR_CODE = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SERVER_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
LIMITATIONS = [
    "CONTENT_IDENTITY_NOT_PROVENANCE",
    "MODEL_IDENTITY_NOT_AUTHENTICATED",
    "PROMPT_DELIVERY_NOT_INDEPENDENTLY_PROVEN",
    "FREE_TEXT_NOT_INTERPRETED",
    "MODEL_CORRECTNESS_NOT_EVALUATED",
]
HIDDEN_REFUSAL = EvidenceError(
    "UNSUPPORTED_PROFILE", "TOOL_NOT_EXPOSED_IN_SELECTED_ARM", "/params/name"
).payload


@dataclass(frozen=True)
class CallBytes:
    request: bytes
    response: bytes


@dataclass(frozen=True)
class VerifiedMaterials:
    final_answer: bytes
    calls: tuple[CallBytes, ...]
    structured_results: tuple[bytes | None, ...]
    artifact_bytes: int
    manifest_bytes: int
    manifest_sha256: str


class _Issue(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _digest(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _strings_with_u202e(value: object) -> list[str]:
    if isinstance(value, str):
        return [value] if "\u202e" in value else []
    if isinstance(value, list):
        return [part for item in cast(list[object], value) for part in _strings_with_u202e(item)]
    if isinstance(value, dict):
        return [
            part
            for item in cast(dict[object, object], value).values()
            for part in _strings_with_u202e(item)
        ]
    return []


def _u202e_covered_by_record_content(answer: str, results: list[dict[str, Any]]) -> bool:
    source_values = {
        part
        for result in results
        if isinstance(result.get("structuredContent"), dict)
        and result["structuredContent"].get("schema") == "record_document.v1"
        for part in _strings_with_u202e(result["structuredContent"].get("content"))
    }
    if len(source_values) > MAX_BIDI_SOURCE_VALUES:
        raise _Issue("RESOURCE_LIMIT_EXCEEDED")
    wanted = {index for index, character in enumerate(answer) if character == "\u202e"}
    covered: set[int] = set()
    matches = 0
    for value in sorted(source_values, key=lambda item: (len(item), item)):
        start = 0
        while (position := answer.find(value, start)) != -1:
            matches += 1
            if matches > MAX_BIDI_MATCHES:
                raise _Issue("RESOURCE_LIMIT_EXCEEDED")
            covered.update(
                position + index for index, character in enumerate(value) if character == "\u202e"
            )
            if wanted <= covered:
                return True
            start = position + 1
    return bool(wanted) and wanted <= covered


def _controls(
    value: object,
    allow_u202e_paths: frozenset[tuple[str, ...]] = frozenset(),
    path: tuple[str, ...] = (),
) -> bool:
    if isinstance(value, str):
        return has_text_controls(value) and any(
            unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"}
            and char not in "\n\r\t"
            and not (
                char == "\u202e"
                and any(path[: len(allowed)] == allowed for allowed in allow_u202e_paths)
            )
            for char in value
        )
    if isinstance(value, list):
        return any(_controls(item, allow_u202e_paths, path) for item in cast(list[object], value))
    if isinstance(value, dict):
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str or _controls(key):
                return True
            child_path = path + (key,)
            if _controls(item, allow_u202e_paths, child_path):
                return True
        return False
    return False


def _record_content_allowance(
    value: object, path: tuple[str, ...] | None
) -> frozenset[tuple[str, ...]]:
    if path is None:
        return frozenset()
    if path[0] == "item":
        if not isinstance(value, dict):
            return frozenset()
        event = cast(dict[str, object], value)
        item = event.get("item")
        if (
            event.get("type") != "item.completed"
            or not isinstance(item, dict)
            or cast(dict[str, object], item).get("type") != "mcp_tool_call"
            or cast(dict[str, object], item).get("tool") != "underwrite_records_read_json"
        ):
            return frozenset()
    parent: object = cast(object, value)
    for part in path[:-1]:
        if not isinstance(parent, dict):
            return frozenset()
        parent = cast(dict[object, object], parent).get(part)
    if (
        isinstance(parent, dict)
        and cast(dict[str, object], parent).get("schema") == "record_document.v1"
    ):
        return frozenset({path})
    return frozenset()


def _json(
    raw: bytes,
    code: str,
    *,
    record_content_path: tuple[str, ...] | None = None,
    allow_agent_message_text: bool = False,
) -> dict[str, Any]:
    try:
        value: object = decode_json(raw, 32)
        allowances = _record_content_allowance(value, record_content_path)
        if allow_agent_message_text and isinstance(value, dict):
            event = cast(dict[str, Any], value)
            item: object = event.get("item")
            if (
                event.get("type") == "item.completed"
                and isinstance(item, dict)
                and cast(dict[str, object], item).get("type") == "agent_message"
            ):
                allowances = allowances | {("item", "text")}
        if _controls(cast(object, value), frozenset(allowances)):
            raise _Issue(code)
        canonical_bytes(cast(object, value))
    except (DecodeError, CanonicalizationError, RecursionError) as exc:
        raise _Issue(code) from exc
    if type(cast(object, value)) is not dict:
        raise _Issue(code)
    return cast(dict[str, Any], value)


def _mapping(value: Any, code: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in cast(dict[Any, Any], value)):
        raise _Issue(code)
    return cast(dict[str, Any], value)


def _manifest(path: Path) -> tuple[dict[str, Any], bytes]:  # noqa: C901
    try:
        raw = read_relative(path.parent, path.name, MAX_BUNDLE_BYTES)
    except ValueError as exc:
        if str(exc) == "UNSAFE_PATH":
            raise ValueError("BUNDLE_PATH_REFUSED") from exc
        raise ValueError("BUNDLE_INVALID") from exc
    try:
        document = _json(raw, "BUNDLE_INVALID")
    except _Issue as exc:
        raise ValueError("BUNDLE_INVALID") from exc
    if (
        set(document)
        != {
            "schema",
            "case_id",
            "arm",
            "suite_digest",
            "catalog_digest",
            "input_ledger_digest",
            "model_event_format",
            "mcp_server",
            "artifacts",
        }
        or document["schema"] != BUNDLE_SCHEMA
        or document["model_event_format"] != MODEL_EVENT_FORMAT
    ):
        raise ValueError("BUNDLE_INVALID")
    if (
        type(document["case_id"]) is not str
        or evaluation.CASE_ID.fullmatch(document["case_id"]) is None
        or document["arm"] not in evaluation.ARMS
        or type(document["mcp_server"]) is not str
        or SERVER_NAME.fullmatch(document["mcp_server"]) is None
        or any(
            type(document[key]) is not str or evaluation.DIGEST.fullmatch(document[key]) is None
            for key in ("suite_digest", "catalog_digest", "input_ledger_digest")
        )
    ):
        raise ValueError("BUNDLE_INVALID")
    try:
        artifacts = _mapping(document["artifacts"], "BUNDLE_INVALID")
    except _Issue as exc:
        raise ValueError("BUNDLE_INVALID") from exc
    if set(artifacts) != set(ARTIFACT_ROLES):
        raise ValueError("BUNDLE_INVALID")
    names: set[str] = set()
    for role in ARTIFACT_ROLES:
        try:
            descriptor = _mapping(artifacts[role], "BUNDLE_INVALID")
        except _Issue as exc:
            raise ValueError("BUNDLE_INVALID") from exc
        if set(descriptor) != {"path", "bytes", "sha256"}:
            raise ValueError("BUNDLE_INVALID")
        name, size, identity = descriptor["path"], descriptor["bytes"], descriptor["sha256"]
        if (
            type(name) is not str
            or not name
            or name in names
            or type(size) is not int
            or size < 0
            or type(identity) is not str
            or evaluation.DIGEST.fullmatch(identity) is None
        ):
            raise ValueError("BUNDLE_INVALID")
        _artifact_path(path.parent, name)
        names.add(name)
    return document, raw


def _artifact_path(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if (
        "\\" in name
        or path.is_absolute()
        or path.as_posix() != name
        or ".." in path.parts
        or any(part in ("", ".") for part in path.parts)
        or any(ord(char) < ASCII_CONTROL_END or ord(char) == ASCII_DELETE for char in name)
    ):
        raise ValueError("BUNDLE_PATH_REFUSED")
    selected = root
    for part in path.parts:
        selected /= part
        if selected.is_symlink():
            raise ValueError("BUNDLE_PATH_REFUSED")
    return selected


def _artifact(root: Path, descriptor: dict[str, Any], role: str) -> bytes:
    name = cast(str, descriptor["path"])
    _artifact_path(root, name)
    try:
        raw = read_relative(root, name, ARTIFACT_LIMITS[role])
    except ValueError as exc:
        if str(exc) == "UNSAFE_PATH":
            raise ValueError("BUNDLE_PATH_REFUSED") from exc
        if str(exc) == "FILE_LIMIT_EXCEEDED":
            raise _Issue("RESOURCE_LIMIT_EXCEEDED") from exc
        raise _Issue("ARTIFACT_MISSING") from exc
    if len(raw) != descriptor["bytes"] or _digest(raw) != descriptor["sha256"]:
        raise _Issue("ARTIFACT_DIGEST_MISMATCH")
    return raw


def _jsonl(
    raw: bytes, limit: int, code: str, *, model_events: bool = False
) -> list[dict[str, Any]]:
    if not raw or not raw.endswith(b"\n"):
        raise _Issue(code)
    lines = raw[:-1].split(b"\n")
    if len(lines) > limit or any(len(line) > MAX_JSONL_LINE_BYTES for line in lines):
        raise _Issue("RESOURCE_LIMIT_EXCEEDED")
    if any(not line for line in lines):
        raise _Issue(code)
    return [
        _json(
            line,
            code,
            record_content_path=("item", "result", "structured_content", "content")
            if model_events
            else None,
            allow_agent_message_text=model_events,
        )
        for line in lines
    ]


def _wire(  # noqa: C901, PLR0912, PLR0915 - one sequential MCP transcript
    raw: bytes,
    expected_catalog: str,
    known_names: set[str],
    exposed_names: set[str],
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], tuple[CallBytes, ...]]:
    records = _jsonl(raw, MAX_WIRE_EVENTS, "WIRE_INVALID")
    pending: tuple[str, Any, dict[str, Any], str, bytes] | None = None
    initialized = False
    notified = False
    catalog_seen = False
    calls: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    call_bytes: list[CallBytes] = []
    for seq, row in enumerate(records):
        if (
            set(row) != {"seq", "direction", "raw_base64"}
            or type(row["seq"]) is not int
            or row["seq"] != seq
        ):
            raise _Issue("WIRE_INVALID")
        encoded = row["raw_base64"]
        if type(encoded) is not str:
            raise _Issue("WIRE_INVALID")
        try:
            message_raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise _Issue("WIRE_INVALID") from exc
        if (
            base64.b64encode(message_raw).decode("ascii") != encoded
            or len(message_raw) > MAX_RAW_MESSAGE_BYTES
        ):
            raise _Issue("WIRE_INVALID")
        direction = row["direction"]
        pending_tool = (
            pending is not None
            and pending[0] == "tools/call"
            and isinstance(pending[2].get("params"), dict)
            and cast(dict[str, object], pending[2]["params"]).get("name")
            == "underwrite_records_read_json"
        )
        message = _json(
            message_raw,
            "WIRE_INVALID",
            record_content_path=("result", "structuredContent", "content")
            if direction == "proxy_to_client" and pending_tool
            else None,
        )
        if message.get("jsonrpc") != "2.0":
            raise _Issue("WIRE_INVALID")
        if direction == "client_to_proxy":
            method = message.get("method")
            if method == "notifications/initialized":
                if not initialized or notified or pending is not None or "id" in message:
                    raise _Issue("WIRE_INVALID")
                notified = True
                continue
            if (
                pending is not None
                or method not in ("initialize", "tools/list", "tools/call")
                or "id" not in message
            ):
                raise _Issue("WIRE_INVALID")
            identifier = message["id"]
            if type(identifier) not in (str, int):
                raise _Issue("WIRE_INVALID")
            if method == "initialize" and initialized or method != "initialize" and not notified:
                raise _Issue("WIRE_INVALID")
            if method == "tools/call" and not catalog_seen:
                raise _Issue("CATALOG_MISSING")
            pending = (cast(str, method), identifier, message, _digest(message_raw), message_raw)
        elif direction == "proxy_to_client":
            if (
                pending is None
                or type(message.get("id")) is not type(pending[1])
                or message.get("id") != pending[1]
                or "result" not in message
                or "error" in message
            ):
                raise _Issue("WIRE_CALL_UNPAIRED")
            method, _identifier, request, request_digest, request_raw = pending
            response_digest = _digest(message_raw)
            result = _mapping(message["result"], "WIRE_TOOL_RESULT_INVALID")
            if method == "initialize":
                initialized = True
            elif method == "tools/list":
                tools = result.get("tools")
                if type(tools) is not list or result.get("nextCursor") is not None:
                    raise _Issue("CATALOG_DIGEST_MISMATCH")
                observed = digest_bytes(canonical_bytes({"tools": cast(list[Any], tools)}))
                catalog.update(
                    observed_digest=observed,
                    response_raw_sha256=response_digest,
                    matched=observed == expected_catalog,
                )
                if observed != expected_catalog:
                    raise _Issue("CATALOG_DIGEST_MISMATCH")
                catalog_seen = True
            else:
                params = _mapping(request.get("params"), "WIRE_INVALID")
                if "name" not in params or not set(params).issubset({"name", "arguments", "_meta"}):
                    raise _Issue("WIRE_INVALID")
                if "_meta" in params:
                    _mapping(params["_meta"], "WIRE_INVALID")
                name, arguments = params["name"], params.get("arguments", {})
                if type(name) is not str or type(arguments) is not dict or name not in known_names:
                    raise _Issue("WIRE_TOOL_RESULT_INVALID")
                arguments = _mapping(arguments, "WIRE_TOOL_RESULT_INVALID")
                if len(calls) >= evaluation.MAX_OBSERVED_CALLS:
                    raise _Issue("RESOURCE_LIMIT_EXCEEDED")
                is_error = result.get("isError")
                content = result.get("content")
                if type(is_error) is not bool or type(content) is not list or not content:
                    raise _Issue("WIRE_TOOL_RESULT_INVALID")
                content_rows = cast(list[Any], content)
                first_content = _mapping(content_rows[0], "WIRE_TOOL_RESULT_INVALID")
                if (
                    len(content_rows) != 1
                    or set(first_content) != {"type", "text"}
                    or first_content["type"] != "text"
                    or type(first_content.get("text")) is not str
                ):
                    raise _Issue("WIRE_TOOL_RESULT_INVALID")
                detail = _json(
                    cast(str, first_content["text"]).encode("utf-8"),
                    "WIRE_TOOL_RESULT_INVALID",
                    record_content_path=("content",)
                    if name == "underwrite_records_read_json"
                    else None,
                )
                if is_error:
                    if (
                        set(result) != {"content", "isError"}
                        or detail.get("schema") != "evidence_error.v1"
                        or "structuredContent" in result
                        or type(detail.get("code")) is not str
                        or ERROR_CODE.fullmatch(detail["code"]) is None
                    ):
                        raise _Issue("WIRE_TYPED_CODE_MISSING")
                    code = detail["code"]
                else:
                    if set(result) != {"content", "structuredContent", "isError"}:
                        raise _Issue("WIRE_TOOL_RESULT_INVALID")
                    structured = _mapping(
                        result.get("structuredContent"), "WIRE_TOOL_RESULT_INVALID"
                    )
                    if canonical_bytes(detail) != canonical_bytes(structured):
                        raise _Issue("WIRE_TOOL_RESULT_INVALID")
                    code = None
                exposed = name in exposed_names
                if not exposed and (
                    not is_error or canonical_bytes(detail) != canonical_bytes(HIDDEN_REFUSAL)
                ):
                    raise _Issue("WIRE_TOOL_RESULT_INVALID")
                calls.append(
                    {
                        "ordinal": len(calls),
                        "name": name,
                        "arguments": arguments,
                        "arguments_digest": digest_bytes(canonical_bytes(arguments)),
                        "request_meta_digest": (
                            digest_bytes(canonical_bytes(params["_meta"]))
                            if "_meta" in params
                            else None
                        ),
                        "request_raw_sha256": request_digest,
                        "response_raw_sha256": response_digest,
                        "is_error": is_error,
                        "typed_code": code,
                        "result_schema": detail.get("schema"),
                        "exposed": exposed,
                        "proxy_refusal_observed": not exposed,
                    }
                )
                results.append(result)
                call_bytes.append(CallBytes(request_raw, message_raw))
            pending = None
        else:
            raise _Issue("WIRE_INVALID")
    if pending is not None:
        raise _Issue("WIRE_CALL_UNPAIRED")
    if not catalog_seen:
        raise _Issue("CATALOG_MISSING")
    return calls, results, tuple(call_bytes)


def _model(  # noqa: C901, PLR0912, PLR0915 - Codex event lifecycle validation
    raw: bytes,
    calls: list[dict[str, Any]],
    results: list[dict[str, Any]],
    final: bytes,
    expected_server: str,
) -> list[dict[str, Any]]:
    events = _jsonl(raw, MAX_MODEL_EVENTS, "MODEL_EVENTS_INVALID", model_events=True)
    if (
        events[-1].get("type") != "turn.completed"
        or sum(event.get("type") == "turn.completed" for event in events) != 1
    ):
        raise _Issue("MODEL_TURN_INCOMPLETE")
    if any(event.get("type") in ("error", "turn.failed") for event in events):
        raise _Issue("MODEL_TURN_INCOMPLETE")
    if (
        len(events) < MIN_MODEL_EVENTS
        or events[0].get("type") != "thread.started"
        or events[1].get("type") != "turn.started"
        or sum(event.get("type") == "thread.started" for event in events) != 1
        or sum(event.get("type") == "turn.started" for event in events) != 1
    ):
        raise _Issue("MODEL_EVENTS_INVALID")
    starts: list[dict[str, Any]] = []
    completions: list[dict[str, Any]] = []
    messages: list[str] = []
    live_items: set[str] = set()
    for event in events:
        kind = event.get("type")
        item = event.get("item")
        if kind not in (
            "thread.started",
            "turn.started",
            "item.started",
            "item.completed",
            "turn.completed",
        ):
            raise _Issue("MODEL_EVENTS_INVALID")
        if kind not in ("item.started", "item.completed"):
            continue
        item = _mapping(item, "MODEL_EVENTS_INVALID")
        item_type = item.get("type")
        if item_type not in ("mcp_tool_call", "agent_message", "reasoning"):
            raise _Issue("MODEL_OTHER_TOOL_OBSERVED")
        if item_type == "mcp_tool_call":
            identifier = item.get("id")
            if type(identifier) is not str:
                raise _Issue("MODEL_EVENTS_INVALID")
            if kind == "item.started":
                if identifier in live_items:
                    raise _Issue("MODEL_EVENTS_INVALID")
                live_items.add(identifier)
            elif identifier not in live_items:
                raise _Issue("WIRE_MODEL_CALL_MISMATCH")
            else:
                live_items.remove(identifier)
            (starts if kind == "item.started" else completions).append(item)
        elif item_type == "agent_message" and kind == "item.completed":
            if type(item.get("text")) is not str:
                raise _Issue("MODEL_EVENTS_INVALID")
            messages.append(item["text"])
    if live_items or len(starts) != len(calls) or len(completions) != len(calls):
        raise _Issue("WIRE_MODEL_CALL_MISMATCH")
    model_calls: list[dict[str, Any]] = []
    for ordinal, (started, completed, wire_call, result) in enumerate(
        zip(starts, completions, calls, results, strict=True)
    ):
        if (
            type(started.get("id")) is not str
            or started["id"] != completed.get("id")
            or started.get("server") != completed.get("server")
            or started.get("server") != expected_server
            or started.get("tool") != completed.get("tool")
            or started.get("tool") != wire_call["name"]
            or type(started.get("arguments")) is not dict
            or type(completed.get("arguments")) is not dict
            or canonical_bytes(started["arguments"]) != canonical_bytes(completed["arguments"])
            or canonical_bytes(started["arguments"]) != canonical_bytes(wire_call["arguments"])
            or completed.get("status") != "completed"
            or completed.get("error") is not None
        ):
            raise _Issue("WIRE_MODEL_CALL_MISMATCH")
        model_result = _mapping(completed.get("result"), "WIRE_MODEL_CALL_MISMATCH")
        if canonical_bytes(model_result.get("content")) != canonical_bytes(
            result["content"]
        ) or canonical_bytes(model_result.get("structured_content")) != canonical_bytes(
            result.get("structuredContent")
        ):
            raise _Issue("WIRE_MODEL_CALL_MISMATCH")
        model_calls.append(
            {
                "ordinal": ordinal,
                "item_id": started["id"],
                "server": started["server"],
                "name": started["tool"],
                "arguments": started["arguments"],
                "arguments_digest": wire_call["arguments_digest"],
                "status": "completed",
            }
        )
    if not messages:
        raise _Issue("FINAL_ANSWER_MISSING")
    if any(_controls(message) for message in messages[:-1]):
        raise _Issue("MODEL_EVENTS_INVALID")
    if _controls(messages[-1]):
        disallowed = {
            char
            for char in messages[-1]
            if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} and char not in "\n\r\t"
        }
        if disallowed != {"\u202e"} or not _u202e_covered_by_record_content(messages[-1], results):
            raise _Issue("MODEL_EVENTS_INVALID")
    try:
        expected = messages[-1].encode("utf-8")
        final.decode("utf-8")
    except UnicodeError as exc:
        raise _Issue("FINAL_ANSWER_MISMATCH") from exc
    if final != expected:
        raise _Issue("FINAL_ANSWER_MISMATCH")
    return model_calls


def verify_materials(
    bundle_path: Path, suite_path: Path, archive_root: Path
) -> tuple[dict[str, Any], VerifiedMaterials | None]:
    """Return one bounded report and its verified raw materials for local review."""
    manifest, manifest_raw = _manifest(bundle_path)
    arm, case_id = manifest["arm"], manifest["case_id"]
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "case_id": case_id,
        "arm": arm,
        "suite_digest": manifest["suite_digest"],
        "catalog_digest": manifest["catalog_digest"],
        "input_ledger_digest": manifest["input_ledger_digest"],
        "mcp_server": manifest["mcp_server"],
        "state": "not_adjudicable",
        "reasons": [],
        "catalog": {
            "expected_digest": evaluation.CATALOGS[arm][1],
            "observed_digest": None,
            "response_raw_sha256": None,
            "matched": False,
        },
        "wire_calls": [],
        "model_calls": [],
        "final_answer": {"bytes": None, "sha256": None, "matched": False},
        "trial_projection": None,
        "limitations": LIMITATIONS,
    }
    try:
        tools = evaluation.load_catalogs()
        inputs, ledger_digest = evaluation.read_ledger(archive_root)
        suite = evaluation.load_suite(suite_path, tools, inputs)
        template = evaluation.trial_template(suite, arm, ledger_digest)
        eligible = {row.case_id for row in evaluation.selected_cases(suite, arm)}
        if (
            manifest["suite_digest"] != template["suite_digest"]
            or manifest["catalog_digest"] != template["catalog_digest"]
            or manifest["input_ledger_digest"] != ledger_digest
            or case_id not in eligible
        ):
            raise _Issue("IDENTITY_MISMATCH")
        artifacts = manifest["artifacts"]
        raw = {
            role: _artifact(bundle_path.parent, artifacts[role], role) for role in ARTIFACT_ROLES
        }
        exposed = {cast(str, row["name"]) for row in tools[arm]}
        known = {cast(str, row["name"]) for row in tools["current_22"]}
        calls, results, call_bytes = _wire(
            raw["wire"],
            cast(str, template["catalog_digest"]),
            known,
            exposed,
            report["catalog"],
        )
        report["wire_calls"] = calls
        report["final_answer"] = {
            "bytes": len(raw["final_answer"]),
            "sha256": _digest(raw["final_answer"]),
            "matched": False,
        }
        model_calls = _model(
            raw["model_events"], calls, results, raw["final_answer"], manifest["mcp_server"]
        )
        report["model_calls"] = model_calls
        report["final_answer"] = {
            "bytes": len(raw["final_answer"]),
            "sha256": _digest(raw["final_answer"]),
            "matched": True,
        }
        report["trial_projection"] = {"case_id": case_id, "calls": [row["name"] for row in calls]}
        report["state"] = "adjudicable"
        return report, VerifiedMaterials(
            raw["final_answer"],
            call_bytes,
            tuple(
                canonical_bytes(result["structuredContent"])
                if result.get("isError") is False
                else None
                for result in results
            ),
            sum(len(value) for value in raw.values()),
            len(manifest_raw),
            _digest(manifest_raw),
        )
    except _Issue as exc:
        report["reasons"] = [exc.code]
        return report, None


def verify(bundle_path: Path, suite_path: Path, archive_root: Path) -> dict[str, Any]:
    """Return a bounded local consistency report for one case in one catalog arm."""
    report, _materials = verify_materials(bundle_path, suite_path, archive_root)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--suite", type=Path, required=True)
    parser.add_argument("--archive-root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = verify(args.bundle, args.suite, args.archive_root)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Bounded client-started stdio MCP projections of read-only CLI results."""

from __future__ import annotations

import io
import json
import math
import sys
from collections.abc import Callable
from contextlib import redirect_stderr, redirect_stdout
from importlib.metadata import version
from typing import BinaryIO, TypeGuard, cast

from underwrite_core.canonical import canonical_bytes

from underwrite._model_json_text import has_text_controls
from underwrite.agent_surface.tools import catalog
from underwrite.cli import (
    audit_baseline,
    audit_counts,
    audit_deepeval,
    audit_langfuse,
    audit_promptfoo,
    capabilities,
    inspect_result,
    inspection,
    record_trajectory,
    records,
    review_artifacts,
    review_pair,
)
from underwrite.cli.evidence import EvidenceError, usage_error

PROTOCOL_VERSION = "2025-11-25"
MAX_REQUEST_BYTES = 64 * 1024
MAX_SAFE_ID = (1 << 53) - 1


def _error(request_id: str | int | None, code: int, message: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _response(request_id: str | int, result: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _safe_id(value: object) -> TypeGuard[str | int]:
    return type(value) is str or (type(value) is int and abs(value) <= MAX_SAFE_ID)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    del value
    raise ValueError("NONFINITE_JSON_NUMBER")


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("NONFINITE_JSON_NUMBER")
    return number


def _decode(line: bytes) -> object:
    return json.loads(
        line.decode("utf-8"),
        object_pairs_hook=_unique_object,
        parse_constant=_invalid_constant,
        parse_float=_finite_float,
    )


def _valid_params(params: object) -> bool:
    if params is None:
        return True
    if not isinstance(params, dict):
        return False
    payload = cast(dict[str, object], params)
    if "_meta" not in payload:
        return True
    metadata = payload["_meta"]
    if not isinstance(metadata, dict):
        return False
    values = cast(dict[str, object], metadata)
    token = values.get("progressToken")
    return "progressToken" not in values or (
        type(token) is str
        or type(token) is int
        and abs(token) <= MAX_SAFE_ID
        or type(token) is float
        and math.isfinite(token)
    )


def _valid_icon(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    icon = cast(dict[str, object], value)
    sizes = icon.get("sizes", [])
    return (
        type(icon.get("src")) is str
        and ("mimeType" not in icon or type(icon["mimeType"]) is str)
        and ("theme" not in icon or icon["theme"] in ("light", "dark"))
        and isinstance(sizes, list)
        and all(type(size) is str for size in cast(list[object], sizes))
    )


def _valid_client_info(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    info = cast(dict[str, object], value)
    if any(type(info.get(field)) is not str for field in ("name", "version")):
        return False
    if any(
        field in info and type(info[field]) is not str
        for field in ("title", "description", "websiteUrl")
    ):
        return False
    icons = info.get("icons", [])
    return isinstance(icons, list) and all(_valid_icon(item) for item in cast(list[object], icons))


def _object_members(value: object, fields: tuple[str, ...]) -> bool:
    if not isinstance(value, dict):
        return False
    payload = cast(dict[str, object], value)
    return all(field not in payload or isinstance(payload[field], dict) for field in fields)


def _valid_task_requests(value: object) -> bool:
    if value is None:
        return True
    if not _object_members(value, ("elicitation", "sampling")):
        return False
    requests = cast(dict[str, object], value)
    return all(
        field not in requests or _object_members(requests[field], members)
        for field, members in (
            ("elicitation", ("create",)),
            ("sampling", ("createMessage",)),
        )
    )


def _valid_client_capabilities(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    capabilities = cast(dict[str, object], value)
    roots = capabilities.get("roots")
    experimental = capabilities.get("experimental")
    tasks = capabilities.get("tasks")
    requests = cast(dict[str, object], tasks).get("requests") if isinstance(tasks, dict) else None
    return (
        all(
            field not in capabilities or isinstance(capabilities[field], dict)
            for field in ("experimental", "roots", "sampling", "elicitation", "tasks")
        )
        and (
            not isinstance(roots, dict)
            or "listChanged" not in roots
            or type(cast(dict[str, object], roots)["listChanged"]) is bool
        )
        and (
            not isinstance(experimental, dict)
            or all(
                isinstance(item, dict) for item in cast(dict[str, object], experimental).values()
            )
        )
        and all(
            field not in capabilities or _object_members(capabilities[field], members)
            for field, members in (
                ("sampling", ("context", "tools")),
                ("elicitation", ("form", "url")),
                ("tasks", ("list", "cancel", "requests")),
            )
        )
        and _valid_task_requests(requests)
    )


def _evidence_error(failure: EvidenceError) -> dict[str, object]:
    text = canonical_bytes(failure.payload).decode("utf-8")
    return {"content": [{"type": "text", "text": text}], "isError": True}


def _tool_error() -> dict[str, object]:
    return _evidence_error(usage_error())


def _json_text(output: str) -> str:
    """Discard only the CLI stream delimiter, not any part of its JSON value."""
    if not output.endswith("\n") or output.count("\n") != 1:
        raise ValueError("INVALID_JSON_LINE")
    text = output[:-1]
    if has_text_controls(text):
        raise ValueError("UNSAFE_JSON_TEXT")
    return text


def _cli_result(
    handler: Callable[[list[str]], int],
    argv: list[str],
    output_schema: str | tuple[str, ...],
    error_schema: str = "evidence_error.v1",
    result_exit_codes: tuple[int, ...] = (0,),
) -> dict[str, object]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = handler(argv)
    output, failure = stdout.getvalue(), stderr.getvalue()
    if exit_code in result_exit_codes and not failure:
        text = _json_text(output)
        document: object = json.loads(output)
        if not isinstance(document, dict):
            raise ValueError("INVALID_CLI_RESULT")
        result = cast(dict[str, object], document)
        output_schemas = (output_schema,) if type(output_schema) is str else output_schema
        schema_name = result.get("schema")
        if type(schema_name) is not str or schema_name not in output_schemas:
            raise ValueError("INVALID_CLI_RESULT")
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": result,
            "isError": False,
        }
    if exit_code != 0 and not output:
        text = _json_text(failure)
        document = json.loads(failure)
        if isinstance(document, dict) and (
            cast(dict[str, object], document).get("schema") == error_schema
        ):
            return {"content": [{"type": "text", "text": text}], "isError": True}
    raise ValueError("INVALID_CLI_STREAMS")


def _inspect_result_result(
    path: str, expected_source: str, view: inspect_result.View
) -> dict[str, object]:
    argv = [path, "--json"]
    if view == "actions":
        argv.extend(("--view", "actions"))
    projected = _cli_result(
        lambda arguments: inspect_result.main(arguments, expected_source=expected_source),
        argv,
        inspect_result.OUTPUT_SCHEMAS[view],
    )
    if projected["isError"]:
        return projected
    report = cast(dict[str, object], projected["structuredContent"])
    selected = cast(dict[str, object], report["selected_result"])
    if selected.get("format") != expected_source:
        raise ValueError("CLI_PROFILE_MISMATCH")
    return projected


class Session:
    """The minimal 2025-11-25 lifecycle for read-only stdio tools."""

    def __init__(self) -> None:
        self.phase = "new"

    def handle(self, message: object) -> dict[str, object] | None:
        if not isinstance(message, dict):
            return _error(None, -32600, "Invalid Request")
        request = cast(dict[str, object], message)
        raw_id = request.get("id")
        request_id = raw_id if _safe_id(raw_id) else None
        method = request.get("method")
        if request.get("jsonrpc") != "2.0" or type(method) is not str:
            return _error(request_id, -32600, "Invalid Request")
        if "id" not in request:
            params = request.get("params")
            if (
                method == "notifications/initialized"
                and self.phase == "initializing"
                and _valid_params(params)
            ):
                self.phase = "ready"
            return None
        if request_id is None:
            return _error(None, -32600, "Invalid Request")
        try:
            return self._dispatch(request_id, method, request.get("params"))
        except Exception as exc:
            # Retain the connection without reflecting input or exception text to the model.
            print(f"underwrite-mcp internal error: {type(exc).__name__}", file=sys.stderr)
            return _error(request_id, -32603, "Internal error")

    def _dispatch(self, request_id: str | int, method: str, params: object) -> dict[str, object]:
        if method == "initialize":
            return self._initialize(request_id, params)
        if method == "ping":
            return self._ping(request_id, params)
        if self.phase != "ready":
            return _error(request_id, -32000, "Not initialized")
        if method == "tools/list":
            return self._list(request_id, params)
        if method == "tools/call":
            return self._call(request_id, params)
        return _error(request_id, -32601, "Method not found")

    @staticmethod
    def _ping(request_id: str | int, params: object) -> dict[str, object]:
        if not _valid_params(params):
            return _error(request_id, -32602, "Invalid params")
        return _response(request_id, {})

    @staticmethod
    def _list(request_id: str | int, params: object) -> dict[str, object]:
        if not _valid_params(params) or isinstance(params, dict) and "cursor" in params:
            return _error(request_id, -32602, "Invalid params")
        return _response(request_id, {"tools": catalog()})

    def _initialize(self, request_id: str | int, params: object) -> dict[str, object]:
        if self.phase != "new":
            return _error(request_id, -32600, "Already initialized")
        if not _valid_params(params) or not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        payload = cast(dict[str, object], params)
        if not isinstance(payload.get("protocolVersion"), str) or not isinstance(
            payload.get("capabilities"), dict
        ):
            return _error(request_id, -32602, "Invalid params")
        if not _valid_client_capabilities(payload["capabilities"]) or not _valid_client_info(
            payload.get("clientInfo")
        ):
            return _error(request_id, -32602, "Invalid params")
        server_version = version("underwrite")
        self.phase = "initializing"
        return _response(
            request_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "underwrite", "version": server_version},
            },
        )

    def _call(self, request_id: str | int, params: object) -> dict[str, object]:
        if not _valid_params(params) or not isinstance(params, dict):
            return _error(request_id, -32602, "Invalid params")
        payload = cast(dict[str, object], params)
        name = payload.get("name")
        tools = catalog()
        if type(name) is not str or name not in {tool["name"] for tool in tools}:
            return _error(request_id, -32602, "Unknown tool")
        if "task" in payload:
            return _error(request_id, -32602, "Invalid params")
        arguments = payload.get("arguments", {})
        if type(arguments) is not dict:
            return _error(request_id, -32602, "Invalid params")
        selected = cast(dict[str, object], arguments)
        return self._invoke_tool(request_id, name, selected)

    def _invoke_tool(
        self, request_id: str | int, name: str, selected: dict[str, object]
    ) -> dict[str, object]:
        if name == "underwrite_capabilities":
            return self._call_capabilities(request_id, selected)
        if name == "underwrite_audit_baseline":
            result = self._call_baseline(request_id, selected)
        elif name in {
            "underwrite_inspect",
            "underwrite_audit_promptfoo",
            "underwrite_audit_counts",
            "underwrite_audit_deepeval",
            "underwrite_audit_deepeval_trace",
            "underwrite_audit_deepeval_bound",
            "underwrite_audit_langfuse",
            "underwrite_audit_langfuse_score_source",
        }:
            result = self._call_source_tools(request_id, name, selected)
        elif name == "underwrite_records_read_trajectory":
            result = self._call_record_read(request_id, selected)
        elif name == "underwrite_review_pair":
            result = self._call_review_pair(request_id, selected)
        elif name == "underwrite_review_artifacts":
            result = self._call_review_artifacts(request_id, selected)
        elif name == "underwrite_review_artifacts_readiness":
            result = self._call_review_artifacts_readiness(request_id, selected)
        elif name in {
            "underwrite_records_list",
            "underwrite_records_show",
            "underwrite_records_verify",
            "underwrite_records_read_json",
        }:
            result = self._call_record_tools(request_id, name, selected)
        elif name in {
            "underwrite_inspect_result_v1",
            "underwrite_inspect_result_v2",
            "underwrite_inspect_result_v1_actions",
            "underwrite_inspect_result_v2_actions",
        }:
            result = self._call_inspection(request_id, name, selected)
        else:
            raise ValueError("UNHANDLED_TOOL")
        return result

    def _call_source_tools(
        self, request_id: str | int, name: str, selected: dict[str, object]
    ) -> dict[str, object]:
        if name == "underwrite_inspect":
            result = self._call_inspect(request_id, selected)
        elif name == "underwrite_audit_promptfoo":
            result = self._call_audit_promptfoo(request_id, selected)
        elif name == "underwrite_audit_counts":
            result = self._call_audit_counts(request_id, selected)
        elif name == "underwrite_audit_deepeval":
            result = self._call_audit_deepeval(request_id, selected)
        elif name == "underwrite_audit_deepeval_trace":
            result = self._call_audit_deepeval_trace(request_id, selected)
        elif name == "underwrite_audit_deepeval_bound":
            result = self._call_audit_deepeval_bound(request_id, selected)
        elif name == "underwrite_audit_langfuse":
            result = self._call_audit_langfuse(request_id, selected)
        elif name == "underwrite_audit_langfuse_score_source":
            result = self._call_audit_langfuse_score_source(request_id, selected)
        else:
            raise ValueError("UNHANDLED_SOURCE_TOOL")
        return result

    def _call_record_tools(
        self, request_id: str | int, name: str, selected: dict[str, object]
    ) -> dict[str, object]:
        if name == "underwrite_records_list":
            return self._call_records_list(request_id, selected)
        if name == "underwrite_records_show":
            return self._call_records_show(request_id, selected)
        if name == "underwrite_records_verify":
            return self._call_records_verify(request_id, selected)
        if name == "underwrite_records_read_json":
            return self._call_records_read_json(request_id, selected)
        raise ValueError("UNHANDLED_RECORD_TOOL")

    @staticmethod
    def _call_capabilities(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        if arguments:
            return _response(request_id, _tool_error())
        return _response(request_id, _cli_result(capabilities.main, ["--json"], "capabilities.v1"))

    @staticmethod
    def _call_inspect(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path = arguments.get("case")
        if set(arguments) != {"case"} or type(path) is not str or not path:
            return _response(
                request_id,
                _cli_result(
                    inspection.main,
                    ["--json"],
                    "artifact_inspection.v1",
                    "artifact_inspection_error.v1",
                    (0, 1),
                ),
            )
        return _response(
            request_id,
            _cli_result(
                inspection.main,
                [path, "--json"],
                "artifact_inspection.v1",
                "artifact_inspection_error.v1",
                (0, 1),
            ),
        )

    @staticmethod
    def _call_audit_promptfoo(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path = arguments.get("file")
        if set(arguments) != {"file"} or type(path) is not str or not path:
            return _response(
                request_id,
                _cli_result(
                    audit_promptfoo.main, ["--json"], audit_promptfoo.SUMMARY_SCHEMA
                ),
            )
        return _response(
            request_id,
            _cli_result(
                audit_promptfoo.main,
                [path, "--view", "summary", "--json"],
                audit_promptfoo.SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_counts(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path = arguments.get("case")
        if set(arguments) != {"case"} or type(path) is not str or not path:
            return _response(
                request_id,
                _cli_result(audit_counts.main, ["--json"], audit_promptfoo.SUMMARY_SCHEMA),
            )
        return _response(
            request_id,
            _cli_result(
                audit_counts.main,
                [path, "--view", "summary", "--json"],
                audit_promptfoo.SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_deepeval(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path = arguments.get("file")
        if set(arguments) != {"file"} or type(path) is not str or not path:
            return _response(
                request_id,
                _cli_result(audit_deepeval.main, ["--json"], audit_deepeval.SUMMARY_SCHEMA),
            )
        return _response(
            request_id,
            _cli_result(
                audit_deepeval.main,
                [path, "--view", "summary", "--json"],
                audit_deepeval.SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_deepeval_trace(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path = arguments.get("file")
        if set(arguments) != {"file"} or type(path) is not str or not path:
            return _response(
                request_id,
                _cli_result(audit_deepeval.main, ["--json"], audit_deepeval.TRACE_SUMMARY_SCHEMA),
            )
        return _response(
            request_id,
            _cli_result(
                audit_deepeval.main,
                [path, "--view", "trace-summary", "--json"],
                audit_deepeval.TRACE_SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_deepeval_bound(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        path, bindings = arguments.get("file"), arguments.get("bindings")
        if (
            set(arguments) != {"file", "bindings"}
            or type(path) is not str
            or not path
            or type(bindings) is not str
            or not bindings
        ):
            return _response(
                request_id,
                _cli_result(audit_deepeval.main, ["--json"], audit_deepeval.BOUND_SUMMARY_SCHEMA),
            )
        return _response(
            request_id,
            _cli_result(
                audit_deepeval.main,
                [path, "--bindings", bindings, "--view", "bound-summary", "--json"],
                audit_deepeval.BOUND_SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_langfuse(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        observations, scores = arguments.get("observations"), arguments.get("scores")
        if (
            set(arguments) != {"observations", "scores"}
            or type(observations) is not str
            or not observations
            or type(scores) is not str
            or not scores
        ):
            return _response(
                request_id,
                _cli_result(audit_langfuse.main, ["--json"], audit_langfuse.SUMMARY_SCHEMA),
            )
        return _response(
            request_id,
            _cli_result(
                audit_langfuse.main,
                [
                    "--observations", observations, "--scores", scores,
                    "--view", "summary", "--json",
                ],
                audit_langfuse.SUMMARY_SCHEMA,
            ),
        )

    @staticmethod
    def _call_audit_langfuse_score_source(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        observations, scores = arguments.get("observations"), arguments.get("scores")
        if (
            set(arguments) != {"observations", "scores"}
            or type(observations) is not str
            or not observations
            or type(scores) is not str
            or not scores
        ):
            return _response(
                request_id,
                _cli_result(
                    audit_langfuse.main, ["--json"], audit_langfuse.AUTO_SCORE_SOURCE_SCHEMA
                ),
            )
        return _response(
            request_id,
            _cli_result(
                audit_langfuse.main,
                [
                    "--observations", observations, "--scores", scores,
                    "--view", "score-source", "--json",
                ],
                audit_langfuse.AUTO_SCORE_SOURCE_SCHEMA,
            ),
        )

    @staticmethod
    def _call_inspection(
        request_id: str | int, name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        file = arguments.get("file")
        if set(arguments) != {"file"} or type(file) is not str or not file:
            return _response(request_id, _tool_error())
        suffix = name.removeprefix("underwrite_inspect_result_")
        version = suffix.split("_", 1)[0]
        view: inspect_result.View = "actions" if suffix.endswith("_actions") else "summary"
        expected_source = "evidence_result." + version
        return _response(request_id, _inspect_result_result(file, expected_source, view))

    @staticmethod
    def _call_baseline(request_id: str | int, arguments: dict[str, object]) -> dict[str, object]:
        exchange = arguments.get("exchange")
        if set(arguments) != {"exchange"} or type(exchange) is not str or not exchange:
            return _response(request_id, _tool_error())
        return _response(
            request_id,
            _cli_result(
                audit_baseline.main,
                ["--exchange", exchange, "--json"],
                audit_baseline.RESULT_SCHEMA,
            ),
        )

    @staticmethod
    def _call_record_read(request_id: str | int, arguments: dict[str, object]) -> dict[str, object]:
        root, identifier = arguments.get("root"), arguments.get("id")
        if (
            set(arguments) != {"root", "id"}
            or type(root) is not str
            or not root
            or type(identifier) is not str
            or not identifier
        ):
            return _response(request_id, _tool_error())
        return _response(
            request_id,
            _cli_result(
                record_trajectory.main_read,
                ["--root", root, "--id", identifier, "--view", "summary", "--json"],
                (record_trajectory.SUMMARY_SCHEMA, record_trajectory.SUMMARY_SCHEMA_V2),
            ),
        )

    @staticmethod
    def _call_review_pair(request_id: str | int, arguments: dict[str, object]) -> dict[str, object]:
        if set(arguments) != {"input", "policy", "context"} or any(
            type(arguments[name]) is not str or not arguments[name]
            for name in ("input", "policy", "context")
        ):
            return _response(request_id, _tool_error())
        argv: list[str] = []
        for name in ("input", "policy", "context"):
            argv.extend(("--" + name, cast(str, arguments[name])))
        argv.extend(("--view", "summary", "--json"))
        return _response(
            request_id,
            _cli_result(review_pair.main, argv, review_pair.SUMMARY_SCHEMA),
        )

    @staticmethod
    def _call_review_artifacts(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        required = {"goppi_assessment", "goppi_result", "promptfoo"}
        if not required <= set(arguments) or set(arguments) - required - {"correspondence"}:
            return _response(request_id, _tool_error())
        if any(type(value) is not str or not value for value in arguments.values()):
            return _response(request_id, _tool_error())
        argv: list[str] = []
        for name in ("goppi_assessment", "goppi_result", "promptfoo", "correspondence"):
            if name in arguments:
                argv.extend(("--" + name.replace("_", "-"), cast(str, arguments[name])))
        argv.extend(("--view", "summary", "--json"))
        return _response(
            request_id,
            _cli_result(review_artifacts.main, argv, review_artifacts.SUMMARY_SCHEMA),
        )

    @staticmethod
    def _call_review_artifacts_readiness(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        required = {"goppi_assessment", "goppi_result", "promptfoo"}
        if set(arguments) != required or any(
            type(arguments[name]) is not str or not arguments[name] for name in required
        ):
            return _response(request_id, _tool_error())
        argv: list[str] = []
        for name in ("goppi_assessment", "goppi_result", "promptfoo"):
            argv.extend(("--" + name.replace("_", "-"), cast(str, arguments[name])))
        argv.extend(("--view", "readiness-summary", "--json"))
        return _response(
            request_id,
            _cli_result(review_artifacts.main, argv, review_artifacts.READINESS_SUMMARY_SCHEMA),
        )

    @staticmethod
    def _record_usage_error(command: str) -> dict[str, object]:
        schema = {
            "list": records.CATALOG_PAGE_SCHEMA,
            "show": records.RECORD_VIEW_SUMMARY_SCHEMA,
            "verify": records.RECORD_VERIFICATION_SUMMARY_SCHEMA,
            "read-json": "record_document.v1",
        }[command]
        return _cli_result(
            records.main,
            [command, "--json"],
            schema,
            "record_error.v1",
        )

    @staticmethod
    def _call_records_list(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        root = arguments.get("root")
        offset = arguments.get("offset", 0)
        digest = arguments.get("catalog_digest")
        if (
            set(arguments) - {"root", "offset", "catalog_digest"}
            or type(root) is not str
            or not root
            or type(offset) is not int
            or "catalog_digest" in arguments and type(digest) is not str
        ):
            return _response(request_id, Session._record_usage_error("list"))
        argv = ["list", "--root", root, "--view", "page", "--offset", str(offset), "--json"]
        if digest is not None:
            argv.extend(("--catalog-digest", cast(str, digest)))
        return _response(
            request_id,
            _cli_result(records.main, argv, records.CATALOG_PAGE_SCHEMA, "record_error.v1"),
        )

    @staticmethod
    def _call_records_show(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        root, identifier = arguments.get("root"), arguments.get("id")
        offset = arguments.get("file_offset", 0)
        digest = arguments.get("manifest_digest")
        if (
            set(arguments) - {"root", "id", "file_offset", "manifest_digest"}
            or type(root) is not str
            or not root
            or type(identifier) is not str
            or not identifier
            or type(offset) is not int
            or "manifest_digest" in arguments and type(digest) is not str
        ):
            return _response(request_id, Session._record_usage_error("show"))
        argv = [
            "show", "--root", root, "--id", identifier, "--view", "summary",
            "--file-offset", str(offset), "--json",
        ]
        if digest is not None:
            argv.extend(("--manifest-digest", cast(str, digest)))
        return _response(
            request_id,
            _cli_result(records.main, argv, records.RECORD_VIEW_SUMMARY_SCHEMA, "record_error.v1"),
        )

    @staticmethod
    def _call_records_verify(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        root, identifier = arguments.get("root"), arguments.get("id")
        if (
            set(arguments) != {"root", "id"}
            or type(root) is not str
            or not root
            or type(identifier) is not str
            or not identifier
        ):
            return _response(request_id, Session._record_usage_error("verify"))
        return _response(
            request_id,
            _cli_result(
                records.main,
                ["verify", "--root", root, "--id", identifier, "--view", "summary", "--json"],
                records.RECORD_VERIFICATION_SUMMARY_SCHEMA,
                "record_error.v1",
            ),
        )

    @staticmethod
    def _call_records_read_json(
        request_id: str | int, arguments: dict[str, object]
    ) -> dict[str, object]:
        root, identifier, name = (
            arguments.get("root"), arguments.get("id"), arguments.get("name")
        )
        if (
            set(arguments) != {"root", "id", "name"}
            or type(root) is not str
            or not root
            or type(identifier) is not str
            or not identifier
            or type(name) is not str
            or not name
        ):
            return _response(request_id, Session._record_usage_error("read-json"))
        return _response(
            request_id,
            _cli_result(
                records.main,
                [
                    "read-json", "--root", root, "--id", identifier,
                    "--name", name, "--view", "bounded",
                ],
                "record_document.v1",
                "record_error.v1",
            ),
        )


def serve(reader: BinaryIO, writer: BinaryIO) -> None:
    """Read bounded JSON lines until EOF; never write non-protocol stdout."""
    session = Session()
    while line := reader.readline(MAX_REQUEST_BYTES + 1):
        if len(line) > MAX_REQUEST_BYTES:
            while line and not line.endswith(b"\n"):
                line = reader.readline(MAX_REQUEST_BYTES + 1)
            response = _error(None, -32600, "Request too large")
        else:
            try:
                response = session.handle(_decode(line))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError, RecursionError):
                response = _error(None, -32700, "Parse error")
        if response is not None:
            writer.write(canonical_bytes(response) + b"\n")
            writer.flush()


def main() -> int:
    serve(sys.stdin.buffer, sys.stdout.buffer)
    return 0

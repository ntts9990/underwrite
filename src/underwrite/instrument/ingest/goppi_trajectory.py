"""Capture and verify one adopted Goppi exchange before descriptive interpretation."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import CanonicalizationError, canonical_bytes

from underwrite.instrument.evidence.goppi_trajectory import (
    DIAGNOSTIC_CODES,
    MAX_REPORT_BYTES,
    MAX_ROWS,
    MAX_SPANS_PER_UNIT,
    MAX_UNITS,
    MEMBERS,
    RESULT_SCHEMA,
    TrajectoryError,
    analyze_goppi_trajectory,
)
from underwrite.instrument.ingest.goppi_verifier import (
    STDOUT_MAX_BYTES,
    GoppiCliVerifier,
    TrajectoryVerifier,
    VerifierError,
)
from underwrite.instrument.ingest.local_capture import (
    CapturedBytes,
    CaptureError,
    CapturePolicy,
    RootReader,
    decode_captured_object,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, decode_json

MAX_FAMILY_BYTES = 64 * 1024 * 1024
MAX_DOCUMENT_BYTES = 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DEPTH = 32
RESPONSE_SCHEMA = "goppi_trajectory_verifier_response.v1"
HISTORY_SCHEMA = "trajectory_verification_history.v1"
CAPTURE_POLICY = CapturePolicy(len(MEMBERS), MAX_FAMILY_BYTES, 128 * 1024 * 1024, 2)

_PRE_VERIFICATION_DIAGNOSTICS = {
    "CAPTURE_INVALID_JSON": "INVALID_INPUT",
    "CAPTURE_JSON_OBJECT_REQUIRED": "INVALID_INPUT",
    "TRAJECTORY_ROW_LIMIT": "INPUT_LIMIT_EXCEEDED",
    "TRAJECTORY_OBJECT_REQUIRED": "INVALID_INPUT",
    "TRAJECTORY_UNITS_REQUIRED": "INVALID_INPUT",
    "TRAJECTORY_UNIT_LIMIT": "INPUT_LIMIT_EXCEEDED",
    "TRAJECTORY_CASES_REQUIRED": "INVALID_INPUT",
    "TRAJECTORY_SPAN_LIMIT": "INPUT_LIMIT_EXCEEDED",
}


def trajectory_diagnostic_history_matches(code: str, reason: str, state: str) -> bool:
    """Whether this source diagnostic belongs to the recorded native verification stage."""
    if state == "not_performed":
        return _PRE_VERIFICATION_DIAGNOSTICS.get(reason) == code
    if state == "verified":
        return DIAGNOSTIC_CODES.get(reason) == code
    return False


class TrajectoryInputError(ValueError):
    """Owned admission failure; no received error text or private paths escape."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def trajectory_member_limits() -> dict[str, int]:
    """Return the source member budgets used by capture and record restoration."""
    return {
        name: MAX_MANIFEST_BYTES
        if name.endswith("/manifest.json")
        else MAX_DOCUMENT_BYTES
        if name.endswith(("/schema.json", "/execution.json"))
        else MAX_FAMILY_BYTES
        for name in MEMBERS
    }


def _capture(exchange: Path) -> dict[str, CapturedBytes]:
    with RootReader.open(exchange, CAPTURE_POLICY) as reader:
        captured = reader.capture_exact_tree(trajectory_member_limits())
    _family_limits(captured)
    return captured


def _family_limits(captured: Mapping[str, CapturedBytes]) -> None:
    if any(
        sum(item.size for name, item in captured.items() if name.startswith(family + "/"))
        > MAX_FAMILY_BYTES
        for family in ("paired", "trajectory")
    ):
        raise TrajectoryInputError("INPUT_LIMIT_EXCEEDED", "TRAJECTORY_FAMILY_BYTE_LIMIT")


def _rows(captured: CapturedBytes) -> tuple[dict[str, object], ...]:
    lines = captured.data.split(b"\n", MAX_ROWS + 1)
    if lines[-1] == b"":
        lines.pop()
    if len(lines) > MAX_ROWS:
        raise TrajectoryInputError("INPUT_LIMIT_EXCEEDED", "TRAJECTORY_ROW_LIMIT")
    result: list[dict[str, object]] = []
    for index, line in enumerate(lines):
        location = f"/exchange/paired/rows.jsonl/{index}"
        try:
            value = decode_json(line, MAX_DEPTH)
        except DecodeError:
            raise CaptureError("INVALID_INPUT", "CAPTURE_INVALID_JSON", location) from None
        if type(value) is not dict:
            raise CaptureError("INVALID_INPUT", "CAPTURE_JSON_OBJECT_REQUIRED", location)
        result.append(cast(dict[str, object], value))
    return tuple(result)


def _object(value: object) -> dict[str, Any]:
    if type(value) is not dict:
        raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_OBJECT_REQUIRED")
    return cast(dict[str, Any], value)


def _resource_limits(trajectory: dict[str, object]) -> None:
    units = trajectory.get("units")
    if not isinstance(units, list):
        raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_UNITS_REQUIRED")
    units = cast(list[object], units)
    if len(units) > MAX_UNITS:
        raise TrajectoryInputError("INPUT_LIMIT_EXCEEDED", "TRAJECTORY_UNIT_LIMIT")
    for unit in units:
        cases: object = _object(unit).get("cases")
        if not isinstance(cases, list):
            raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_CASES_REQUIRED")
        spans = 0
        for case in cast(list[object], cases):
            retained: object = _object(case).get("spans")
            if isinstance(retained, list):
                spans += len(cast(list[object], retained))
        if spans > MAX_SPANS_PER_UNIT:
            raise TrajectoryInputError("INPUT_LIMIT_EXCEEDED", "TRAJECTORY_SPAN_LIMIT")


def _validate(validator: Draft202012Validator, value: object, *, response: bool) -> None:
    try:
        validator.validate(value)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        if response:
            raise TrajectoryInputError(
                "UNSUPPORTED_FORMAT", "GOPPI_VERIFIER_RESPONSE_UNSUPPORTED"
            ) from None
        raise SchemaConfigurationError("TRAJECTORY_RESULT_CONTRACT_FAILED") from None
    except Exception as exc:
        raise SchemaConfigurationError("TRAJECTORY_SCHEMA_EVALUATION_FAILED") from exc


def _response(
    raw: bytes,
    validator: Draft202012Validator,
    snapshot: Path,
    documents: Mapping[str, dict[str, object]],
    row_count: int,
) -> None:
    if type(raw) is not bytes or len(raw) > STDOUT_MAX_BYTES:
        raise TrajectoryInputError("OUTPUT_LIMIT_EXCEEDED", "GOPPI_VERIFIER_OUTPUT_LIMIT")
    try:
        value = decode_json(raw, MAX_DEPTH)
    except DecodeError:
        raise TrajectoryInputError("INVALID_INPUT", "GOPPI_VERIFIER_RESPONSE_INVALID") from None
    _validate(validator, value, response=True)
    envelope = _object(value)
    data = _object(envelope["data"])
    companion = _object(data["trajectory"])
    integer_values = (
        envelope["exit_code"],
        data["row_count"],
        *_object(companion["availability_counts"]).values(),
    )
    if any(type(value) is not int for value in integer_values):
        raise TrajectoryInputError("UNSUPPORTED_FORMAT", "GOPPI_VERIFIER_RESPONSE_UNSUPPORTED")
    execution = documents["paired/execution.json"]
    trajectory = documents["trajectory/trajectory.json"]
    bindings = (
        (data["input"], str(snapshot)),
        (data["paired_output"], str(snapshot / "paired")),
        (companion["output"], str(snapshot / "trajectory")),
        (data["manifest_digest"], documents["paired/manifest.json"].get("content_digest")),
        (companion["manifest_digest"], documents["trajectory/manifest.json"].get("content_digest")),
        (data["row_count"], row_count),
        (data["cycle_status"], _object(execution.get("source")).get("cycle_status")),
        (companion["availability_counts"], trajectory.get("availability_counts")),
        (companion["capture_profile"], trajectory.get("profile")),
    )
    if any(not _same_binding(actual, expected) for actual, expected in bindings):
        raise TrajectoryInputError("INPUT_BINDING_MISMATCH", "GOPPI_VERIFIER_BINDING_MISMATCH")


def _same_binding(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        left, right = cast(dict[str, object], actual), _object(expected)
        return left.keys() == right.keys() and all(
            _same_binding(left[key], right[key]) for key in left
        )
    return actual == expected


def _write_snapshot(snapshot: Path, captured: Mapping[str, CapturedBytes]) -> None:
    os.chmod(snapshot, 0o700)
    for family in ("paired", "trajectory"):
        (snapshot / family).mkdir(mode=0o700)
    for name, item in captured.items():
        descriptor = os.open(snapshot / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(item.data)


def _check_snapshot(snapshot: Path, captured: Mapping[str, CapturedBytes]) -> None:
    try:
        checked = _capture(snapshot)
    except CaptureError as exc:
        if exc.reason not in {"CAPTURE_TOPOLOGY_INVALID", "CAPTURE_TOPOLOGY_CHANGED"}:
            raise
        raise TrajectoryInputError(
            "INPUT_BINDING_MISMATCH", "GOPPI_VERIFIER_SNAPSHOT_CHANGED"
        ) from None
    if any(checked[name].data != item.data for name, item in captured.items()):
        raise TrajectoryInputError("INPUT_BINDING_MISMATCH", "GOPPI_VERIFIER_SNAPSHOT_CHANGED")


@dataclass(frozen=True)
class TrajectoryAnalysis:
    """One analysis outcome and the bounded history of its native verification."""

    report: dict[str, object] | None
    error: CaptureError | TrajectoryInputError | TrajectoryError | None
    verification_history: dict[str, object]

    def __post_init__(self) -> None:
        if (self.report is None) == (self.error is None):
            raise ValueError("TRAJECTORY_ANALYSIS_REQUIRES_ONE_OUTCOME")


def _contracts() -> tuple[Draft202012Validator, Draft202012Validator, Draft202012Validator]:
    return (
        packaged_validator(RESULT_SCHEMA),
        packaged_validator(RESPONSE_SCHEMA),
        packaged_validator(HISTORY_SCHEMA),
    )


def _validated_capture(captured: Mapping[str, CapturedBytes]) -> Mapping[str, CapturedBytes]:
    # Copy the mapping before validation; callers cannot replace a member during analysis.
    frozen = dict(captured)
    if frozen.keys() != set(MEMBERS):
        raise TrajectoryInputError(
            "INVALID_INPUT", "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED", "/exchange"
        )
    limits = trajectory_member_limits()
    for name, item in frozen.items():
        if (
            type(item) is not CapturedBytes
            or type(item.data) is not bytes
            or type(item.size) is not int
            or type(item.logical_name) is not str
            or type(item.sha256) is not str
            or item.logical_name != name
            or item.size != len(item.data)
        ):
            raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_CAPTURE_IDENTITY_INVALID")
        if item.size > min(limits[name], CAPTURE_POLICY.max_file_bytes):
            raise CaptureError("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_LIMIT_EXCEEDED", name)
        if item.sha256 != "sha256:" + hashlib.sha256(item.data).hexdigest():
            raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_CAPTURE_IDENTITY_INVALID")
    if len(frozen) > CAPTURE_POLICY.max_files:
        raise CaptureError("INPUT_LIMIT_EXCEEDED", "CAPTURE_FILE_COUNT_EXCEEDED")
    if sum(item.size for item in frozen.values()) > CAPTURE_POLICY.max_total_bytes:
        raise CaptureError("INPUT_LIMIT_EXCEEDED", "CAPTURE_TOTAL_LIMIT_EXCEEDED")
    _family_limits(frozen)
    return MappingProxyType(frozen)


def capture_goppi_trajectory(exchange: Path) -> Mapping[str, CapturedBytes]:
    """Capture the complete source once, loading required contracts before file effects."""
    _contracts()
    try:
        captured = _capture(exchange)
    except CaptureError as exc:
        if exc.reason != "CAPTURE_TOPOLOGY_INVALID":
            raise
        raise TrajectoryInputError(
            "INVALID_INPUT", "TRAJECTORY_COMPLETE_HANDOFF_REQUIRED", "/exchange"
        ) from None
    return MappingProxyType(captured)


def _history(
    captured: Mapping[str, CapturedBytes],
    *,
    verified: bool,
    validator: Draft202012Validator,
) -> dict[str, object]:
    history: dict[str, object] = {
        "schema": HISTORY_SCHEMA,
        "state": "verified" if verified else "not_performed",
        "source_members": [
            {"name": name, "raw_hash": captured[name].sha256, "bytes": captured[name].size}
            for name in MEMBERS
        ],
        "integrity_scope": "self_consistent_content_closure" if verified else None,
        "authenticity": "content_digest_only" if verified else None,
        "source_authentication_independently_verified": False,
        "producer_projection_independently_verified": False,
    }
    _validate(validator, history, response=False)
    return history


def audit_captured_goppi_trajectory(
    captured: Mapping[str, CapturedBytes], *, verifier: TrajectoryVerifier | None = None
) -> TrajectoryAnalysis:
    """Analyze fixed captured bytes, retaining only explicitly scoped source diagnostics.

    Verifier/process, snapshot, contract and result-encoding failures always raise.
    History describes this call, not independent source authenticity or a future rerun.
    """
    result_validator, response_validator, history_validator = _contracts()
    captured = _validated_capture(captured)
    history = _history(captured, verified=False, validator=history_validator)
    try:
        documents = {
            name: decode_captured_object(item, max_depth=MAX_DEPTH, location="/exchange/" + name)
            for name, item in captured.items()
            if name.endswith(".json") and not name.endswith("/schema.json")
        }
        rows = _rows(captured["paired/rows.jsonl"])
        trajectory = documents["trajectory/trajectory.json"]
        _resource_limits(trajectory)
    except (CaptureError, TrajectoryInputError) as exc:
        if not trajectory_diagnostic_history_matches(exc.code, exc.reason, "not_performed"):
            raise
        return TrajectoryAnalysis(None, exc, history)
    selected = verifier if verifier is not None else GoppiCliVerifier()
    try:
        with tempfile.TemporaryDirectory(prefix="underwrite-trajectory-") as temporary:
            snapshot = Path(temporary).resolve()
            _write_snapshot(snapshot, captured)
            raw = selected.verify(snapshot)
            _response(raw, response_validator, snapshot, documents, len(rows))
            _check_snapshot(snapshot, captured)
    except VerifierError as exc:
        raise TrajectoryInputError(exc.code, exc.reason) from None
    except OSError:
        raise TrajectoryInputError("SOURCE_READ_FAILED", "TRAJECTORY_SNAPSHOT_FAILED") from None
    history = _history(captured, verified=True, validator=history_validator)
    try:
        report = analyze_goppi_trajectory(
            documents["paired/execution.json"],
            rows,
            trajectory,
            {name: (item.sha256, item.size) for name, item in captured.items()},
        )
    except TrajectoryError as exc:
        if not trajectory_diagnostic_history_matches(exc.code, exc.reason, "verified"):
            raise
        return TrajectoryAnalysis(None, exc, history)
    _object(report["trust_scope"]).update(
        native_verification="local_goppi_same_byte_snapshot",
        integrity_scope="self_consistent_content_closure",
        authenticity="content_digest_only",
    )
    _validate(result_validator, report, response=False)
    try:
        size = len(canonical_bytes(report)) + 1
    except CanonicalizationError:
        raise TrajectoryInputError("INVALID_INPUT", "TRAJECTORY_RESULT_ENCODING_FAILED") from None
    if size > MAX_REPORT_BYTES:
        raise TrajectoryInputError("OUTPUT_LIMIT_EXCEEDED", "TRAJECTORY_REPORT_LIMIT")
    return TrajectoryAnalysis(report, None, history)


def audit_goppi_trajectory(
    exchange: Path, *, verifier: TrajectoryVerifier | None = None
) -> dict[str, object]:
    """Verify captured bytes with the native owner, preserving the standalone API.

    The private snapshot's permissions do not attest the original directory's
    privacy. The installed executable is trusted software supplied by the operator.
    """
    analysis = audit_captured_goppi_trajectory(
        capture_goppi_trajectory(exchange), verifier=verifier
    )
    if analysis.error is not None:
        raise analysis.error
    assert analysis.report is not None
    return analysis.report

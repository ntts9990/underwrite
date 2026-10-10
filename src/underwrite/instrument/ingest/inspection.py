"""One local artifact inspection, without evaluation or release authority."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import content_digest, digest_bytes

from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_DEPTH,
    DIAGNOSTICS,
    NEXT_ACTIONS,
    instrument_diagnostic,
    packaged_ingestor,
)
from underwrite.instrument.ingest.formats import trusted_codecs
from underwrite.instrument.ingest.project import Projection, Source, validate_source
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import (
    DecodeError,
    TransportError,
    UnsupportedFormat,
    decode_json,
    read_descriptor,
)

MANIFEST_MAX_BYTES = 64 * 1024
MANIFEST_MAX_DEPTH = 16
CONFIGURATION_EXIT = 2
LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "RELEASE_BINDING_NOT_CHECKED",
    "MEASUREMENT_NOT_PERFORMED",
    "NO_DEPLOYMENT_DECISION",
)


class InspectionError(ValueError):
    """Safe structured invocation failure; no received text is exposed."""

    def __init__(self, code: str, reason: str, location: str, action: str) -> None:
        super().__init__(code)
        self.payload: dict[str, object] = {
            "schema": "artifact_inspection_error.v1",
            **diagnostic(code, reason, location, action),
            "exit_code": 2,
        }


def diagnostic(code: str, reason: str, location: str, action: str) -> dict[str, object]:
    return {
        "code": code,
        "reason": reason,
        "location": location,
        "next_action": action,
        "retryable": False,
    }


def invocation_error(error: Exception, location: str) -> InspectionError:
    if isinstance(error, SchemaConfigurationError) and error.args[0] in {
        "WRONG_INSPECTION_CONTRACT",
        "INVALID_INSPECTION_SCHEMA",
        "INSPECTION_SCHEMA_EVALUATION_FAILED",
    }:
        return InspectionError(
            "SCHEMA_CONFIGURATION_ERROR",
            str(error.args[0]),
            "",
            NEXT_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
        )
    detail = instrument_diagnostic(error, "inspect")
    code, reason = str(detail["code"]), str(detail["reason"])
    action = "CORRECT_MANIFEST"
    if code == "UNSUPPORTED_FORMAT":
        action = "SELECT_SUPPORTED_PROFILE"
    elif code in {"SCHEMA_CONFIGURATION_ERROR", "SOURCE_READ_FAILED"}:
        action = NEXT_ACTIONS[code]
    elif code == "INPUT_LIMIT_EXCEEDED" and location == "/artifact/path":
        action = "CORRECT_ARTIFACT"
    return InspectionError(code, reason, location, action)


def constrained_flags() -> tuple[int, int]:
    required = [
        getattr(os, name, None) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "O_NOCTTY")
    ]
    if any(flag is None for flag in required) or os.open not in os.supports_dir_fd:
        raise InspectionError(
            "UNSUPPORTED_PLATFORM",
            "UNSUPPORTED_PLATFORM",
            "",
            NEXT_ACTIONS["UNSUPPORTED_PLATFORM"],
        )
    directory, nofollow, nonblock, noctty = cast(list[int], required)
    return os.O_RDONLY | directory | nofollow, os.O_RDONLY | nofollow | nonblock | noctty


def read_at(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
    """Every component is opened from the previous descriptor, without following links."""
    if type(limit) is not int or limit < 0:
        raise ValueError("READ_ALLOWANCE_MUST_BE_NONNEGATIVE_INTEGER")
    opened: list[int] = []
    attempted_read = False
    try:
        current = parent
        for component in parts[:-1]:
            current = os.open(component, flags[0], dir_fd=current)
            opened.append(current)
        leaf = os.open(parts[-1], flags[1], dir_fd=current)
        opened.append(leaf)
        attempted_read = True
        if limit:
            data = read_descriptor(leaf, limit)
        else:
            if not stat.S_ISREG(os.fstat(leaf).st_mode):
                raise TransportError("SOURCE_NOT_REGULAR_FILE")
            data = os.read(leaf, 1)
        if len(data) > limit:
            raise TransportError("BYTE_LIMIT_EXCEEDED")
        return data
    except OSError as exc:
        error = TransportError("SOURCE_READ_FAILED")
        error.attempted_read = attempted_read
        raise error from exc
    except TransportError as exc:
        exc.attempted_read = attempted_read
        raise
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def _manifest(raw: bytes) -> dict[str, object]:
    validator = packaged_validator("artifact_case.v1")
    try:
        value = decode_json(raw, MANIFEST_MAX_DEPTH)
    except DecodeError as exc:
        detail = instrument_diagnostic(exc, "inspect")
        code = (
            "INPUT_LIMIT_EXCEEDED"
            if detail["code"] == "INPUT_LIMIT_EXCEEDED"
            else "INVALID_MANIFEST"
        )
        raise InspectionError(code, str(detail["reason"]), "", "CORRECT_MANIFEST") from exc
    except Exception as exc:
        raise SchemaConfigurationError("INSPECTION_SCHEMA_EVALUATION_FAILED") from exc
    return _validate_artifact_case(value, validator)


def validate_artifact_case(value: object) -> dict[str, object]:
    """Validate the authoritative case structure, including safe relative paths."""
    return _validate_artifact_case(value, packaged_validator("artifact_case.v1"))


def _validate_artifact_case(value: object, validator: Draft202012Validator) -> dict[str, object]:
    try:
        validator.validate(value)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError as exc:
        if list(exc.absolute_path) == ["artifact", "path"]:
            raise InspectionError(
                "INVALID_ARTIFACT_PATH",
                "INVALID_ARTIFACT_PATH",
                "/artifact/path",
                "CORRECT_MANIFEST",
            ) from exc
        raise InspectionError(
            "INVALID_MANIFEST", "INVALID_MANIFEST", "", "CORRECT_MANIFEST"
        ) from exc
    except Exception as exc:
        raise SchemaConfigurationError("INSPECTION_SCHEMA_EVALUATION_FAILED") from exc
    return cast(dict[str, object], value)


def case_source(case: dict[str, object]) -> Source:
    """Apply existing Source semantics after structural case validation."""
    metadata = cast(dict[str, str], case["source"])
    source = Source(
        metadata["format"], metadata["format_version"], metadata.get("ref"), metadata.get("locator")
    )
    validate_source(source)
    return source


def inspect_bytes(
    raw: bytes, source: Source, expected: object
) -> tuple[dict[str, object], list[dict[str, object]], Projection | None]:
    """Summarize one complete buffer and retain its original codec facts transiently."""
    raw_hash = digest_bytes(raw)
    check = (
        "not_supplied" if expected is None else "matched" if expected == raw_hash else "mismatched"
    )
    observations: dict[str, object] | None = None
    diagnostics: list[dict[str, object]] = []
    projection: Projection | None = None
    if check == "mismatched":
        diagnostics.append(
            diagnostic(
                "EXPECTED_HASH_MISMATCH",
                "EXPECTED_HASH_MISMATCH",
                "/artifact/expected_hash",
                "CHECK_EXPECTED_HASH",
            )
        )
    else:
        try:
            ingestor = packaged_ingestor(max_bytes=DEFAULT_MAX_BYTES, max_depth=DEFAULT_MAX_DEPTH)
            result, projection = ingestor.buffered_projection(raw, "local artifact buffer", source)
            observation = result.observation
            observations = {"kind": observation["kind"], "digest": content_digest(observation)}
        except tuple(DIAGNOSTICS) as exc:
            detail = instrument_diagnostic(exc, "inspect")
            if detail["exit_code"] == CONFIGURATION_EXIT:
                raise invocation_error(exc, "") from exc
            diagnostics.append(
                diagnostic(
                    str(detail["code"]), str(detail["reason"]), "/artifact", "CORRECT_ARTIFACT"
                )
            )
    return (
        {
            "raw_hash": raw_hash,
            "observation": observations,
            "expected_hash_check": check,
            "projection": "created" if observations is not None else "not_created",
        },
        diagnostics,
        projection,
    )


def _report_with_projection(
    manifest: bytes, raw: bytes, source: Source, expected: object
) -> tuple[dict[str, object], Projection | None]:
    artifact, diagnostics, projection = inspect_bytes(raw, source, expected)
    return {
        "schema": "artifact_inspection.v1",
        "manifest_hash": digest_bytes(manifest),
        "source": {"format": source.format, "format_version": source.format_version},
        **artifact,
        "diagnostics": diagnostics,
        "limitations": list(LIMITATIONS),
    }, projection


def _report(manifest: bytes, raw: bytes, source: Source, expected: object) -> dict[str, object]:
    return _report_with_projection(manifest, raw, source, expected)[0]


def _read_case(
    path: Path, *, required_source: tuple[str, str] | None = None
) -> tuple[bytes, bytes, Source, object]:
    """Read the manifest and artifact once under one caller-trusted directory anchor."""
    flags = constrained_flags()
    if "\x00" in str(path):
        raise InspectionError(
            "SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "", NEXT_ACTIONS["SOURCE_READ_FAILED"]
        )
    location = ""
    try:
        # Parent ancestors are caller-trusted. Only the manifest leaf is untrusted.
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            manifest_bytes = read_at(parent, [path.name], MANIFEST_MAX_BYTES, flags)
            case = _manifest(manifest_bytes)
            location = "/source"
            source = case_source(case)
            if (source.format, source.format_version) not in trusted_codecs():
                raise UnsupportedFormat("UNSUPPORTED_FORMAT")
            if (
                required_source is not None
                and (source.format, source.format_version) != required_source
            ):
                raise UnsupportedFormat("UNSUPPORTED_FORMAT")
            artifact = cast(dict[str, object], case["artifact"])
            location = "/artifact/path"
            parts = cast(str, artifact["path"]).split("/")
            raw = read_at(parent, parts, DEFAULT_MAX_BYTES, flags)
            return manifest_bytes, raw, source, artifact.get("expected_hash")
        finally:
            os.close(parent)
    except InspectionError:
        raise
    except tuple(DIAGNOSTICS) as exc:
        raise invocation_error(exc, location) from exc
    except OSError as exc:
        raise InspectionError(
            "SOURCE_READ_FAILED",
            "SOURCE_READ_FAILED",
            location,
            NEXT_ACTIONS["SOURCE_READ_FAILED"],
        ) from exc


def inspect_artifact_with_projection(
    path: Path, *, required_source: tuple[str, str] | None = None
) -> tuple[dict[str, object], Projection | None]:
    """Expose the validated projection from the same bounded artifact buffer."""
    return _report_with_projection(*_read_case(path, required_source=required_source))


def inspect_artifact(path: Path) -> dict[str, object]:
    """Keep the established report contract and its single buffered artifact read."""
    return _report(*_read_case(path))

"""Two bounded local inputs, one pure read, and fixed packaged schema localization."""

from __future__ import annotations

import argparse
import copy
import json
import sys
from importlib.resources import files
from pathlib import Path
from typing import Any, NoReturn, cast

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from underwrite_core.canonical import canonical_bytes

from underwrite.cli.local_json import Validator, read_document, validate
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import (
    SchemaConfigurationError,
    _check_local_references,  # pyright: ignore[reportPrivateUsage] -- approved unchanged guard
    packaged_validator,
)
from underwrite.instrument.ingest.transport import decode_json
from underwrite.measurement.read import assemble_read
from underwrite.measurement.read_input import READ_INPUT_REASONS, ReadInputError

OBSERVATION_MAX_BYTES = 4 * 1024 * 1024
OBSERVATION_MAX_DEPTH = 64
POLICY_MAX_BYTES = 64 * 1024
POLICY_MAX_DEPTH = 16
OUTPUT_MAX_BYTES = 1024 * 1024
DISPLAY_LIMIT = 1024
_SEED = "urn:underwrite:contract:statistical_seed.v1"
_SUBJECT = "urn:underwrite:contract:read.v1#/$defs/subject"
_ACTIONS = {
    **NEXT_ACTIONS,
    "INVALID_OBSERVATION": "CORRECT_OBSERVATION",
    "INVALID_POLICY": "PROVIDE_COMPLETE_POLICY",
    "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
    "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
    "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
}


class MeasurementError(ValueError):
    """Only fixed internal codes/reasons reach this shell error envelope."""

    def __init__(self, code: str, reason: str, location: str = "") -> None:
        self.payload: dict[str, object] = {
            "schema": "measurement_error.v1",
            "code": code,
            "reason": reason,
            "location": location,
            "next_action": _ACTIONS[code],
            "retryable": False,
            "exit_code": 2,
        }
        super().__init__(code)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise MeasurementError("USAGE_ERROR", "INVALID_ARGUMENTS_SEE_HELP")


def configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--observation", required=True, type=Path, help="Local observation.v1.")
    parser.add_argument(
        "--policy", required=True, type=Path, help="Complete measurement_policy.v2."
    )
    parser.add_argument("--json", action="store_true", help="Canonical read on stdout.")
    parser.description = (
        "Compute one local measurement; exit 0 means completion, not approval. "
        f"Observation limits: {OBSERVATION_MAX_BYTES} bytes / depth {OBSERVATION_MAX_DEPTH}; "
        f"policy: {POLICY_MAX_BYTES} bytes / depth {POLICY_MAX_DEPTH}; "
        f"output: {OUTPUT_MAX_BYTES} bytes including newline."
    )


def _preflight(schema: dict[str, Any], name: str) -> None:
    permitted: dict[tuple[str, tuple[str, ...]], str] = {
        ("read.v1", ("properties", "seed")): _SEED,
        ("measurement_policy.v2", ("properties", "seed")): _SEED,
        ("measurement_policy.v2", ("properties", "subject")): _SUBJECT,
    }
    pending: list[tuple[Any, tuple[str, ...]]] = [(schema, ())]
    while pending:
        node, path = pending.pop()
        if isinstance(node, dict):
            if (
                (path and "$id" in node)
                or "$dynamicRef" in node
                or (name == "statistical_seed.v1" and "$ref" in node)
            ):
                raise SchemaConfigurationError("FORBIDDEN_SCHEMA_REFERENCE")
            if "$ref" in node:
                ref: object = cast("dict[str, Any]", node)["$ref"]
                expected = permitted.get((name, path))
                if expected is not None and node != {"$ref": expected}:
                    raise SchemaConfigurationError("WRONG_MEASUREMENT_REFERENCE")
                if expected is None and (not isinstance(ref, str) or not ref.startswith("#")):
                    raise SchemaConfigurationError("FORBIDDEN_SCHEMA_REFERENCE")
            pending.extend(
                (value, (*path, key)) for key, value in cast("dict[str, Any]", node).items()
            )
        elif isinstance(node, list):
            pending.extend(
                (value, (*path, str(index))) for index, value in enumerate(cast("list[Any]", node))
            )
    for (owner, path), ref in permitted.items():
        if owner == name and schema.get(path[0], {}).get(path[1]) != {"$ref": ref}:
            raise SchemaConfigurationError("MISSING_MEASUREMENT_REFERENCE")


def _subject_fragment(read: dict[str, Any]) -> dict[str, Any]:
    subject = read["$defs"]["subject"]
    if not isinstance(subject, dict):
        raise SchemaConfigurationError("INVALID_SUBJECT_OWNER")
    pending: list[Any] = [subject]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            if any(
                key in node for key in ("$ref", "$id", "$dynamicRef", "$dynamicAnchor", "$anchor")
            ):
                raise SchemaConfigurationError("DEPENDENT_SUBJECT_OWNER")
            pending.extend(cast("dict[str, Any]", node).values())
        elif isinstance(node, list):
            pending.extend(cast("list[Any]", node))
    return cast("dict[str, Any]", subject)


def measurement_validators() -> tuple[Validator, Validator]:
    """Localize only fixed trusted seed/subject positions; never fetch a URI."""
    try:
        assets: dict[str, dict[str, Any]] = {}
        for name in ("read.v1", "measurement_policy.v2", "statistical_seed.v1"):
            resource = files("underwrite").joinpath("_contracts", f"{name}.schema.json")
            value = decode_json(resource.read_bytes(), 64)
            if not isinstance(value, dict):
                raise SchemaConfigurationError("WRONG_MEASUREMENT_CONTRACT")
            schema = cast("dict[str, Any]", value)
            if schema.get("$id") != f"urn:underwrite:contract:{name}":
                raise SchemaConfigurationError("WRONG_MEASUREMENT_CONTRACT")
            _preflight(schema, name)
            assets[name] = schema
        read, policy = assets["read.v1"], assets["measurement_policy.v2"]
        subject = _subject_fragment(read)
        seed = copy.deepcopy(assets["statistical_seed.v1"])
        if seed.get("type") != "integer" or type(seed.get("const")) is not int:
            raise SchemaConfigurationError("INVALID_SEED_OWNER")
        del seed["$id"]
        compiled: list[Validator] = []
        for original in (read, policy):
            schema = copy.deepcopy(original)
            schema["properties"]["seed"] = copy.deepcopy(seed)
            if original is policy:
                schema["properties"]["subject"] = copy.deepcopy(subject)
            _check_local_references(schema)
            Draft202012Validator.check_schema(schema)
            compiled.append(Draft202012Validator(schema))
        return compiled[0], compiled[1]
    except (OSError, ValueError, KeyError, TypeError, AttributeError, SchemaError) as exc:
        raise SchemaConfigurationError("INVALID_MEASUREMENT_SCHEMA") from exc


def _read(args: argparse.Namespace) -> dict[str, Any]:
    observation, observation_digest = read_document(
        args.observation,
        OBSERVATION_MAX_BYTES,
        OBSERVATION_MAX_DEPTH,
        "/observation",
        "INVALID_OBSERVATION",
        MeasurementError,
    )
    policy, policy_digest = read_document(
        args.policy,
        POLICY_MAX_BYTES,
        POLICY_MAX_DEPTH,
        "/policy",
        "INVALID_POLICY",
        MeasurementError,
    )
    read_validator, policy_validator = measurement_validators()
    validate(
        packaged_validator("observation.v1"),
        observation,
        MeasurementError,
        "INVALID_OBSERVATION",
        "/observation",
    )
    if policy.get("schema") == "measurement_policy.v1":
        raise MeasurementError("INVALID_POLICY", "COMPLETE_V2_POLICY_REQUIRED", "/policy")
    if (
        policy.get("schema") != "measurement_policy.v2"
        or policy.get("profile") != "binary-calibration-two-stage.v1"
    ):
        raise MeasurementError("UNSUPPORTED_PROFILE", "UNSUPPORTED_POLICY_PROFILE", "/policy")
    validate(policy_validator, policy, MeasurementError, "INVALID_POLICY", "/policy")
    result = assemble_read(
        observation, policy, observation_digest=observation_digest, policy_digest=policy_digest
    )
    validate(read_validator, result, MeasurementError, "INTERNAL_ERROR")
    return result


def display(value: object) -> str:
    """One JSON value for human output, cut at ``DISPLAY_LIMIT`` characters and so marked."""
    text = json.dumps(value, ensure_ascii=True, sort_keys=True)
    return text if len(text) <= DISPLAY_LIMIT else text[:DISPLAY_LIMIT] + " [truncated]"


def _human(read: dict[str, Any]) -> str:
    lines = ["Measurement completed; exit 0 is not approval. Source claims remain unverified."]
    for key in (
        "subject",
        "verdict",
        "score",
        "escalate",
        "reason_codes",
        "availability",
        "signals",
        "eprocess",
        "jury",
        "calibration",
        "strata",
        "sources",
    ):
        lines.append(f"{key}: " + display(read[key]))
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = _Parser(prog="underwrite measure", allow_abbrev=False)
    configure(parser)
    try:
        args = parser.parse_args(argv)
        read = _read(args)
        output = (canonical_bytes(read).decode() if args.json else _human(read)) + "\n"
        if len(output.encode()) > OUTPUT_MAX_BYTES:
            raise MeasurementError("OUTPUT_LIMIT_EXCEEDED", "OUTPUT_LIMIT_EXCEEDED")
    except ReadInputError as exc:
        if (
            exc.code
            in {
                "INVALID_OBSERVATION",
                "INVALID_POLICY",
                "UNSUPPORTED_PROFILE",
                "INPUT_LIMIT_EXCEEDED",
            }
            and exc.reason in READ_INPUT_REASONS
            and exc.location in {"/observation", "/policy"}
        ):
            error = MeasurementError(exc.code, exc.reason, exc.location)
        else:
            error = MeasurementError("INTERNAL_ERROR", "INTERNAL_ERROR")
    except MeasurementError as exc:
        error = exc
    except SchemaConfigurationError:
        error = MeasurementError("SCHEMA_CONFIGURATION_ERROR", "INVALID_MEASUREMENT_SCHEMA")
    except Exception:
        error = MeasurementError("INTERNAL_ERROR", "INTERNAL_ERROR")
    else:
        sys.stdout.write(output)
        return 0
    sys.stderr.write(canonical_bytes(error.payload).decode() + "\n")
    return 2

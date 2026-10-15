"""Shared measurement shell flow for commands that consume observation and policy."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Literal

from underwrite.cli.local_json import Validator, validate
from underwrite.instrument.ingest.application import NEXT_ACTIONS
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.measurement.event_read import (
    EVENT_READ_REASONS,
    EventReadError,
    assemble_event_read,
)
from underwrite.measurement.read_input import READ_INPUT_REASONS, ReadInputError
from underwrite.measurement_contract.semantics import PROFILE

OBSERVATION_MAX_BYTES = 4 * 1024 * 1024
OBSERVATION_MAX_DEPTH = 64
POLICY_MAX_BYTES = 64 * 1024
POLICY_MAX_DEPTH = 16
OUTPUT_MAX_BYTES = 1024 * 1024
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


InputRole = Literal["observation", "policy"]
InputReader = Callable[[InputRole], tuple[dict[str, Any], str]]


def input_limits(role: InputRole) -> tuple[int, int]:
    """The measurement input role owns its byte and JSON depth bounds."""
    if role == "observation":
        return OBSERVATION_MAX_BYTES, OBSERVATION_MAX_DEPTH
    return POLICY_MAX_BYTES, POLICY_MAX_DEPTH


def measurement_validators() -> tuple[Validator, Validator]:
    """Current closed contracts from trusted package assets; never resolve source URIs."""
    return packaged_validator("read.v2"), packaged_validator("measurement_policy.v3")


def read_measurement(reader: InputReader) -> dict[str, Any]:
    """Consume both inputs, then construct validators and validate in that order."""
    observation, observation_digest = reader("observation")
    policy, policy_digest = reader("policy")
    read_validator, policy_validator = measurement_validators()
    validate(
        packaged_validator("observation.v1"),
        observation,
        MeasurementError,
        "INVALID_OBSERVATION",
        "/observation",
    )
    if policy.get("schema") in {"measurement_policy.v1", "measurement_policy.v2"}:
        raise MeasurementError("INVALID_POLICY", "LEGACY_POLICY_REQUIRES_MIGRATION", "/policy")
    if policy.get("schema") != "measurement_policy.v3" or policy.get("profile") != PROFILE:
        raise MeasurementError("UNSUPPORTED_PROFILE", "UNSUPPORTED_POLICY_PROFILE", "/policy")
    validate(policy_validator, policy, MeasurementError, "INVALID_POLICY", "/policy")
    result = assemble_event_read(
        observation, policy, observation_digest=observation_digest, policy_digest=policy_digest
    )
    validate(read_validator, result, MeasurementError, "INTERNAL_ERROR")
    return result


def measurement_error(exc: Exception) -> MeasurementError:
    """Translate finite domain reasons; callers propagate configuration faults first."""
    if isinstance(exc, (ReadInputError, EventReadError)):
        if (
            exc.code
            in {
                "INVALID_OBSERVATION",
                "INVALID_POLICY",
                "UNSUPPORTED_PROFILE",
                "INPUT_LIMIT_EXCEEDED",
            }
            and exc.reason
            in (EVENT_READ_REASONS if isinstance(exc, EventReadError) else READ_INPUT_REASONS)
            and exc.location in {"/observation", "/policy"}
        ):
            error = MeasurementError(exc.code, exc.reason, exc.location)
        else:
            error = MeasurementError("INTERNAL_ERROR", "INTERNAL_ERROR")
    elif isinstance(exc, MeasurementError):
        error = exc
    elif isinstance(exc, SchemaConfigurationError):
        error = MeasurementError("SCHEMA_CONFIGURATION_ERROR", "INVALID_MEASUREMENT_SCHEMA")
    else:
        error = MeasurementError("INTERNAL_ERROR", "INTERNAL_ERROR")
    return error

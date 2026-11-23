"""Application error identities and byte-compatible error projections.

An action belongs to an envelope context as well as a public code. Add aliases
only after proving equivalent failure meaning; remove an alias only with a
versioned output-contract migration. Neither an alias nor a source verdict grants
decision authority.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final, NamedTuple


class FailureKind(StrEnum):
    INPUT = "input"
    STORAGE = "storage"
    SOURCE = "source"
    LIMIT = "limit"
    UNSUPPORTED = "unsupported"
    USAGE = "usage"
    CONFIGURATION = "configuration"
    INTERNAL = "internal"
    INTEGRITY = "integrity"


class ErrorContext(StrEnum):
    RECORD = "record"


@dataclass(frozen=True)
class CodeIdentity:
    kind: FailureKind


@dataclass(frozen=True)
class ErrorProjection:
    reason: str
    next_action: str
    exit_code: int


@dataclass(frozen=True)
class ActionProjection:
    next_action: str
    exit_code: int


class UnknownErrorProjection(ValueError):
    """A fixed refusal that never includes caller-supplied code or context text."""

    def __init__(self) -> None:
        super().__init__("UNKNOWN_ERROR_PROJECTION")


class _RecordDefinition(NamedTuple):
    reason: str
    action_override: str | None


RECORD_EXIT_CODE: Final = 2
MEASUREMENT_EXIT_CODE: Final = 2
EVIDENCE_EXIT_CODE: Final = 2
ACCEPTANCE_EXIT_CODE: Final = 2
INSPECTION_EXIT_CODE: Final = 2
RELEASE_EXIT_CODE: Final = 2

BASE_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "SCHEMA_CONFIGURATION_ERROR": "REINSTALL_PACKAGE",
        "SOURCE_READ_FAILED": "PROVIDE_REGULAR_LOCAL_FILE",
        "USAGE_ERROR": "CORRECT_ARGUMENTS",
        "INTERNAL_ERROR": "REPORT_INTERNAL_ERROR",
        "UNSUPPORTED_PLATFORM": "USE_SUPPORTED_PLATFORM",
    }
)

ERROR_IDENTITIES: Final[Mapping[str, CodeIdentity]] = MappingProxyType(
    {
        "INVALID_SPEC": CodeIdentity(FailureKind.INPUT),
        "INVALID_RECORD": CodeIdentity(FailureKind.INPUT),
        "INVALID_STORE_LAYOUT": CodeIdentity(FailureKind.STORAGE),
        "INSECURE_STORE": CodeIdentity(FailureKind.STORAGE),
        "STORE_NOT_FOUND": CodeIdentity(FailureKind.STORAGE),
        "RECORD_NOT_FOUND": CodeIdentity(FailureKind.STORAGE),
        "RECORD_EXISTS": CodeIdentity(FailureKind.STORAGE),
        "RECORD_CAPTURE_INCOMPLETE": CodeIdentity(FailureKind.STORAGE),
        "RECORD_NOT_PUBLISHED": CodeIdentity(FailureKind.STORAGE),
        "SOURCE_READ_FAILED": CodeIdentity(FailureKind.SOURCE),
        "INPUT_LIMIT_EXCEEDED": CodeIdentity(FailureKind.LIMIT),
        "RECORD_LIMIT_EXCEEDED": CodeIdentity(FailureKind.LIMIT),
        "STORE_IO_ERROR": CodeIdentity(FailureKind.STORAGE),
        "UNSUPPORTED_PLATFORM": CodeIdentity(FailureKind.UNSUPPORTED),
        "USAGE_ERROR": CodeIdentity(FailureKind.USAGE),
        "SCHEMA_CONFIGURATION_ERROR": CodeIdentity(FailureKind.CONFIGURATION),
        "OUTPUT_LIMIT_EXCEEDED": CodeIdentity(FailureKind.LIMIT),
        "INTERNAL_ERROR": CodeIdentity(FailureKind.INTERNAL),
        "RECORD_FILE_NOT_FOUND": CodeIdentity(FailureKind.STORAGE),
        "RECORD_FILE_INTEGRITY_MISMATCH": CodeIdentity(FailureKind.INTEGRITY),
        "UNSUPPORTED_RECORD_CONTENT": CodeIdentity(FailureKind.UNSUPPORTED),
        "INVALID_OBSERVATION": CodeIdentity(FailureKind.INPUT),
        "INVALID_SOURCE": CodeIdentity(FailureKind.INPUT),
        "CANONICAL_NUMBER_INCOMPATIBLE": CodeIdentity(FailureKind.INPUT),
        "INVALID_POLICY": CodeIdentity(FailureKind.INPUT),
        "UNSUPPORTED_PROFILE": CodeIdentity(FailureKind.UNSUPPORTED),
        "INVALID_INPUT": CodeIdentity(FailureKind.INPUT),
        "UNSUPPORTED_FORMAT": CodeIdentity(FailureKind.UNSUPPORTED),
        "EXPECTED_HASH_MISMATCH": CodeIdentity(FailureKind.INTEGRITY),
        "INVALID_MANIFEST": CodeIdentity(FailureKind.INPUT),
        "INVALID_ARTIFACT_PATH": CodeIdentity(FailureKind.INPUT),
        "COMPUTATION_LIMIT_EXCEEDED": CodeIdentity(FailureKind.LIMIT),
        "INPUT_BINDING_MISMATCH": CodeIdentity(FailureKind.INPUT),
        "AUTHORITY_FIELD_REFUSED": CodeIdentity(FailureKind.INPUT),
        "INVALID_CANDIDATE": CodeIdentity(FailureKind.INPUT),
        "INVALID_CLAIMS": CodeIdentity(FailureKind.INPUT),
        "INVALID_READ": CodeIdentity(FailureKind.INPUT),
        "SELECTION_LEAKS_CONFIRM": CodeIdentity(FailureKind.INPUT),
        "CANDIDATE_MISMATCH": CodeIdentity(FailureKind.INPUT),
        "CLAIMS_DIGEST_MISMATCH": CodeIdentity(FailureKind.INPUT),
        "UNEXPECTED_CLAIM": CodeIdentity(FailureKind.INPUT),
        "DUPLICATE_CLAIM": CodeIdentity(FailureKind.INPUT),
        "READ_POLICY_MISMATCH": CodeIdentity(FailureKind.INPUT),
        "READ_NOT_AGGREGATE": CodeIdentity(FailureKind.INPUT),
        "READ_AVAILABILITY_INCONSISTENT": CodeIdentity(FailureKind.INPUT),
        "INVALID_STATE_FOR_EVALUATION": CodeIdentity(FailureKind.INPUT),
        "READ_COUNT_EXCEEDED": CodeIdentity(FailureKind.LIMIT),
    }
)

# No two currently registered public record codes have proved identical meaning.
ALIASES: Final[Mapping[str, str]] = MappingProxyType({})


INSTRUMENT_EXIT_CODES: Final[Mapping[str, int]] = MappingProxyType(
    {
        "UNSUPPORTED_FORMAT": 1,
        "INVALID_INPUT": 1,
        "INVALID_SOURCE": 1,
        "CANONICAL_NUMBER_INCOMPATIBLE": 1,
        "INPUT_LIMIT_EXCEEDED": 1,
        "INVALID_OBSERVATION": 1,
        "SOURCE_READ_FAILED": 2,
        "SCHEMA_CONFIGURATION_ERROR": 2,
        "USAGE_ERROR": 2,
    }
)


def instrument_exit_code(code: str) -> int:
    """Resolve the closed instrument error code after adapter reason selection."""
    if type(code) is not str:
        raise UnknownErrorProjection
    exit_code = INSTRUMENT_EXIT_CODES.get(code)
    if exit_code is None:
        raise UnknownErrorProjection
    return exit_code


ACCEPTANCE_ROOT_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "candidate": "CORRECT_CANDIDATE",
        "claims": "CORRECT_CLAIMS",
        "reads": "CORRECT_READ",
    }
)

ACCEPTANCE_LOCATION_ROOTS: Final[Mapping[str, frozenset[str]]] = MappingProxyType(
    {
        "AUTHORITY_FIELD_REFUSED": frozenset(("candidate", "claims")),
        "INVALID_CANDIDATE": frozenset(("candidate",)),
        "INVALID_CLAIMS": frozenset(("claims",)),
        "INVALID_READ": frozenset(("reads",)),
        "SELECTION_LEAKS_CONFIRM": frozenset(("claims",)),
        "CANDIDATE_MISMATCH": frozenset(("candidate",)),
        "CLAIMS_DIGEST_MISMATCH": frozenset(("candidate",)),
        "UNEXPECTED_CLAIM": frozenset(("reads",)),
        "DUPLICATE_CLAIM": frozenset(("reads",)),
        "READ_POLICY_MISMATCH": frozenset(("reads",)),
        "READ_NOT_AGGREGATE": frozenset(("reads",)),
        "READ_AVAILABILITY_INCONSISTENT": frozenset(("reads",)),
        "INVALID_STATE_FOR_EVALUATION": frozenset(("candidate",)),
    }
)

ACCEPTANCE_FIXED_ACTIONS: Final[Mapping[str, ActionProjection]] = MappingProxyType(
    {
        code: ActionProjection(action, ACCEPTANCE_EXIT_CODE)
        for code, action in {
            **BASE_ACTIONS,
            "READ_COUNT_EXCEEDED": "REDUCE_INPUT_SIZE",
            "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
            "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        }.items()
    }
)

ACCEPTANCE_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {code: projection.next_action for code, projection in ACCEPTANCE_FIXED_ACTIONS.items()}
)


def acceptance_projection(code: str, location: str) -> ActionProjection:
    """Resolve a closed acceptance action from code and structural input root."""
    if type(code) is not str or type(location) is not str:
        raise UnknownErrorProjection
    fixed = ACCEPTANCE_FIXED_ACTIONS.get(code)
    if fixed is not None:
        return fixed
    roots = ACCEPTANCE_LOCATION_ROOTS.get(code)
    root = location.split("/")[1] if location.startswith("/") else ""
    if roots is None or root not in roots:
        raise UnknownErrorProjection
    return ActionProjection(ACCEPTANCE_ROOT_ACTIONS[root], ACCEPTANCE_EXIT_CODE)


INSPECTION_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "UNSUPPORTED_FORMAT": "SELECT_SUPPORTED_PROFILE",
        "INVALID_MANIFEST": "CORRECT_MANIFEST",
        "INVALID_SOURCE": "CORRECT_MANIFEST",
        "INPUT_LIMIT_EXCEEDED": "CORRECT_MANIFEST",
        "SOURCE_READ_FAILED": BASE_ACTIONS["SOURCE_READ_FAILED"],
        "SCHEMA_CONFIGURATION_ERROR": BASE_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
        "USAGE_ERROR": "CORRECT_MANIFEST",
        "INVALID_ARTIFACT_PATH": "CORRECT_MANIFEST",
        "UNSUPPORTED_PLATFORM": BASE_ACTIONS["UNSUPPORTED_PLATFORM"],
        "INTERNAL_ERROR": BASE_ACTIONS["INTERNAL_ERROR"],
    }
)


def inspection_projection(code: str, location: str) -> ActionProjection:
    """Resolve an invocation error; artifact byte limits identify artifact work."""
    if type(code) is not str or type(location) is not str:
        raise UnknownErrorProjection
    action = INSPECTION_ACTIONS.get(code)
    if action is None:
        raise UnknownErrorProjection
    if code == "INPUT_LIMIT_EXCEEDED" and location == "/artifact/path":
        action = "CORRECT_ARTIFACT"
    return ActionProjection(action, INSPECTION_EXIT_CODE)


RELEASE_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "INVALID_MANIFEST": "CORRECT_MANIFEST",
        "INVALID_SOURCE": "CORRECT_MANIFEST",
        "INPUT_LIMIT_EXCEEDED": "CORRECT_MANIFEST",
        "OUTPUT_LIMIT_EXCEEDED": "REDUCE_BATCH_SIZE",
        "SOURCE_READ_FAILED": BASE_ACTIONS["SOURCE_READ_FAILED"],
        "SCHEMA_CONFIGURATION_ERROR": BASE_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
        "USAGE_ERROR": "CORRECT_MANIFEST",
        "INVALID_ARTIFACT_PATH": "CORRECT_MANIFEST",
        "UNSUPPORTED_PLATFORM": BASE_ACTIONS["UNSUPPORTED_PLATFORM"],
        "INTERNAL_ERROR": BASE_ACTIONS["INTERNAL_ERROR"],
    }
)


def release_projection(code: str, context: str = "default") -> ActionProjection:
    """Resolve release errors with distinct manifest, artifact and batch limits."""
    if type(code) is not str or type(context) is not str:
        raise UnknownErrorProjection
    action = RELEASE_ACTIONS.get(code)
    if action is None:
        raise UnknownErrorProjection
    if context != "default":
        if code != "INPUT_LIMIT_EXCEEDED":
            raise UnknownErrorProjection
        if context == "batch":
            action = "REDUCE_BATCH_SIZE"
        elif context == "artifact":
            action = "CORRECT_ARTIFACT"
        else:
            raise UnknownErrorProjection
    return ActionProjection(action, RELEASE_EXIT_CODE)


_RECORD_DEFINITIONS: Final[Mapping[str, _RecordDefinition]] = MappingProxyType(
    {
        "INVALID_SPEC": _RecordDefinition("INVALID_RECORD_SPEC", "CORRECT_RECORD_SPEC"),
        "INVALID_RECORD": _RecordDefinition("INVALID_RECORD_MANIFEST", "SELECT_VALID_RECORD"),
        "INVALID_STORE_LAYOUT": _RecordDefinition("INVALID_STORE_LAYOUT", "SELECT_VALID_STORE"),
        "INSECURE_STORE": _RecordDefinition("INSECURE_STORE_PERMISSIONS", "SECURE_STORE"),
        "STORE_NOT_FOUND": _RecordDefinition("STORE_NOT_FOUND", "CREATE_OR_SELECT_STORE"),
        "RECORD_NOT_FOUND": _RecordDefinition("RECORD_NOT_FOUND", "SELECT_EXISTING_RECORD"),
        "RECORD_EXISTS": _RecordDefinition("RECORD_ALREADY_EXISTS", "USE_NEW_RECORD_ID"),
        "RECORD_CAPTURE_INCOMPLETE": _RecordDefinition(
            "RECORD_CAPTURE_INCOMPLETE", "USE_NEW_RECORD_ID"
        ),
        "RECORD_NOT_PUBLISHED": _RecordDefinition(
            "RECORD_NOT_PUBLISHED", "SELECT_PUBLISHED_RECORD"
        ),
        "SOURCE_READ_FAILED": _RecordDefinition("SOURCE_READ_FAILED", "CHECK_SOURCE_FILE"),
        "INPUT_LIMIT_EXCEEDED": _RecordDefinition("INPUT_LIMIT_EXCEEDED", "REDUCE_INPUT_SIZE"),
        "RECORD_LIMIT_EXCEEDED": _RecordDefinition("RECORD_LIMIT_EXCEEDED", "REDUCE_RECORD_COUNT"),
        "STORE_IO_ERROR": _RecordDefinition("STORE_IO_ERROR", "CHECK_STORE_IO"),
        "UNSUPPORTED_PLATFORM": _RecordDefinition("UNSUPPORTED_PLATFORM", None),
        "USAGE_ERROR": _RecordDefinition("INVALID_ARGUMENTS_SEE_HELP", None),
        "SCHEMA_CONFIGURATION_ERROR": _RecordDefinition("INVALID_PACKAGED_CONTRACT", None),
        "OUTPUT_LIMIT_EXCEEDED": _RecordDefinition("OUTPUT_LIMIT_EXCEEDED", "REDUCE_OUTPUT_SIZE"),
        "INTERNAL_ERROR": _RecordDefinition("INTERNAL_ERROR", None),
        "RECORD_FILE_NOT_FOUND": _RecordDefinition(
            "RECORD_FILE_NOT_LISTED", "SELECT_LISTED_RECORD_FILE"
        ),
        "RECORD_FILE_INTEGRITY_MISMATCH": _RecordDefinition(
            "RECORD_FILE_INTEGRITY_MISMATCH", "SELECT_VALID_RECORD"
        ),
        "UNSUPPORTED_RECORD_CONTENT": _RecordDefinition(
            "RECORD_CONTENT_NOT_REPRESENTABLE_AS_JSON", "SELECT_SUPPORTED_RECORD_FILE"
        ),
    }
)


def record_projection(code: str, context: ErrorContext) -> ErrorProjection:
    """Resolve a closed record failure; unknown code or context is never success."""
    if type(code) is not str or context is not ErrorContext.RECORD:
        raise UnknownErrorProjection
    definition = _RECORD_DEFINITIONS.get(code)
    if definition is None:
        raise UnknownErrorProjection
    action = definition.action_override
    return ErrorProjection(
        definition.reason,
        BASE_ACTIONS[code] if action is None else action,
        RECORD_EXIT_CODE,
    )


RECORD_PROJECTIONS: Final[Mapping[str, ErrorProjection]] = MappingProxyType(
    {code: record_projection(code, ErrorContext.RECORD) for code in _RECORD_DEFINITIONS}
)


_MEASUREMENT_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        **BASE_ACTIONS,
        "INVALID_OBSERVATION": "CORRECT_OBSERVATION",
        "INVALID_POLICY": "PROVIDE_COMPLETE_POLICY",
        "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
        "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
    }
)

MEASUREMENT_PROJECTIONS: Final[Mapping[str, ActionProjection]] = MappingProxyType(
    {
        code: ActionProjection(action, MEASUREMENT_EXIT_CODE)
        for code, action in _MEASUREMENT_ACTIONS.items()
    }
)


def measurement_projection(code: str) -> ActionProjection:
    """Resolve a closed measurement failure without echoing an unknown code."""
    if type(code) is not str:
        raise UnknownErrorProjection
    projection = MEASUREMENT_PROJECTIONS.get(code)
    if projection is None:
        raise UnknownErrorProjection
    return projection


EVIDENCE_ACTIONS: Final[Mapping[str, str]] = MappingProxyType(
    {
        **BASE_ACTIONS,
        "INVALID_INPUT": "CORRECT_INPUT",
        "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
        "UNSUPPORTED_FORMAT": "USE_SUPPORTED_PROFILE",
        "EXPECTED_HASH_MISMATCH": "CHECK_EXPECTED_HASH",
        "INVALID_MANIFEST": "CORRECT_MANIFEST",
        "INVALID_ARTIFACT_PATH": "CORRECT_MANIFEST",
        "COMPUTATION_LIMIT_EXCEEDED": "REDUCE_COMPUTATION",
        "INPUT_BINDING_MISMATCH": "CORRECT_INPUT_BINDING",
    }
)

EVIDENCE_PROJECTIONS: Final[Mapping[str, ActionProjection]] = MappingProxyType(
    {
        code: ActionProjection(action, EVIDENCE_EXIT_CODE)
        for code, action in EVIDENCE_ACTIONS.items()
    }
)

EVIDENCE_CONTEXT_ACTIONS: Final[Mapping[tuple[str, str], str]] = MappingProxyType(
    {("SOURCE_READ_FAILED", "verifier_executable"): "CORRECT_ARGUMENTS"}
)


def evidence_projection(code: str, context: str = "default") -> ActionProjection:
    """Resolve a closed evidence failure and its one executable-selection context."""
    if type(code) is not str or type(context) is not str:
        raise UnknownErrorProjection
    projection = EVIDENCE_PROJECTIONS.get(code)
    if projection is None:
        raise UnknownErrorProjection
    if context == "default":
        return projection
    action = EVIDENCE_CONTEXT_ACTIONS.get((code, context))
    if action is None:
        raise UnknownErrorProjection
    return ActionProjection(action, projection.exit_code)

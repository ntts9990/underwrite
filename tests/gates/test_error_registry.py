"""The pure error owner projects record failures without changing v1 envelopes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
RECORD_ERROR_EXIT = 2


def test_record_projection_is_the_runtime_and_schema_owner() -> None:
    from underwrite.error_contract.registry import (
        ACCEPTANCE_FIXED_ACTIONS,
        ACCEPTANCE_LOCATION_ROOTS,
        BASE_ACTIONS,
        ERROR_IDENTITIES,
        EVIDENCE_PROJECTIONS,
        INSPECTION_ACTIONS,
        INSTRUMENT_EXIT_CODES,
        MEASUREMENT_PROJECTIONS,
        RECORD_EXIT_CODE,
        RECORD_PROJECTIONS,
        RELEASE_ACTIONS,
        ErrorContext,
        record_projection,
    )
    from underwrite.instrument.ingest.application import NEXT_ACTIONS

    schema = json.loads((ROOT / "contracts/record_error.v1.schema.json").read_bytes())
    assert NEXT_ACTIONS is BASE_ACTIONS
    assert set(ERROR_IDENTITIES) == (
        set(RECORD_PROJECTIONS)
        | set(MEASUREMENT_PROJECTIONS)
        | set(EVIDENCE_PROJECTIONS)
        | set(INSTRUMENT_EXIT_CODES)
        | set(ACCEPTANCE_FIXED_ACTIONS)
        | set(ACCEPTANCE_LOCATION_ROOTS)
        | set(INSPECTION_ACTIONS)
        | set(RELEASE_ACTIONS)
    )
    assert set(RECORD_PROJECTIONS) == set(schema["$defs"])
    assert schema["properties"]["exit_code"]["const"] == RECORD_EXIT_CODE == RECORD_ERROR_EXIT
    for code, projection in RECORD_PROJECTIONS.items():
        assert projection == record_projection(code, ErrorContext.RECORD)
        assert projection.exit_code == RECORD_ERROR_EXIT
        fields = schema["$defs"][code]["properties"]
        assert {key: fields[key]["const"] for key in ("code", "reason", "next_action")} == {
            "code": code,
            "reason": projection.reason,
            "next_action": projection.next_action,
        }


def test_instrument_exit_codes_match_schema_and_adapter() -> None:
    from underwrite.error_contract.registry import INSTRUMENT_EXIT_CODES, instrument_exit_code
    from underwrite.instrument.ingest.application import instrument_diagnostic

    schema = json.loads((ROOT / "contracts/instrument_error.v1.schema.json").read_bytes())
    assert set(INSTRUMENT_EXIT_CODES) == set(schema["properties"]["code"]["enum"])
    assert set(INSTRUMENT_EXIT_CODES.values()) == set(schema["properties"]["exit_code"]["enum"])
    elevated = set(schema["allOf"][0]["if"]["properties"]["code"]["enum"])
    elevated_exit = schema["allOf"][0]["then"]["properties"]["exit_code"]["const"]
    assert {
        code for code, exit_code in INSTRUMENT_EXIT_CODES.items() if exit_code == elevated_exit
    } == elevated
    for code, exit_code in INSTRUMENT_EXIT_CODES.items():
        assert instrument_exit_code(code) == exit_code
    from underwrite.instrument.ingest.transport import UnsupportedFormat

    actual = instrument_diagnostic(UnsupportedFormat("UNSUPPORTED_FORMAT"), "ingest")
    assert actual["exit_code"] == INSTRUMENT_EXIT_CODES["UNSUPPORTED_FORMAT"]


def test_acceptance_registry_matches_schema_and_location_actions() -> None:
    from underwrite.acceptance.decision import REFUSAL_ACTIONS
    from underwrite.error_contract.registry import (
        ACCEPTANCE_FIXED_ACTIONS,
        ACCEPTANCE_LOCATION_ROOTS,
        ACCEPTANCE_ROOT_ACTIONS,
        acceptance_projection,
    )

    schema = json.loads((ROOT / "contracts/acceptance_error.v1.schema.json").read_bytes())
    assert set(ACCEPTANCE_FIXED_ACTIONS) | set(ACCEPTANCE_LOCATION_ROOTS) == set(
        schema["properties"]["code"]["enum"]
    )
    assert REFUSAL_ACTIONS is ACCEPTANCE_ROOT_ACTIONS
    actions = {projection.next_action for projection in ACCEPTANCE_FIXED_ACTIONS.values()}
    for code, roots in ACCEPTANCE_LOCATION_ROOTS.items():
        for root in roots:
            projection = acceptance_projection(code, f"/{root}")
            assert projection.next_action == ACCEPTANCE_ROOT_ACTIONS[root]
            actions.add(projection.next_action)
    assert actions == set(schema["properties"]["next_action"]["enum"])
    assert {projection.exit_code for projection in ACCEPTANCE_FIXED_ACTIONS.values()} == {
        schema["properties"]["exit_code"]["const"]
    }


def test_inspection_registry_matches_schema_and_artifact_context() -> None:
    from underwrite.error_contract.registry import INSPECTION_ACTIONS, inspection_projection

    schema = json.loads((ROOT / "contracts/artifact_inspection_error.v1.schema.json").read_bytes())
    assert set(INSPECTION_ACTIONS) == set(schema["properties"]["code"]["enum"])
    actions = {inspection_projection(code, "").next_action for code in INSPECTION_ACTIONS}
    artifact_limit = inspection_projection("INPUT_LIMIT_EXCEEDED", "/artifact/path")
    assert artifact_limit.next_action == "CORRECT_ARTIFACT"
    actions.add(artifact_limit.next_action)
    assert actions == set(schema["properties"]["next_action"]["enum"])
    assert {inspection_projection(code, "").exit_code for code in INSPECTION_ACTIONS} == {
        schema["properties"]["exit_code"]["const"]
    }


def test_release_registry_matches_schema_and_limit_contexts() -> None:
    from underwrite.error_contract.registry import RELEASE_ACTIONS, release_projection

    schema = json.loads((ROOT / "contracts/release_inspection_error.v1.schema.json").read_bytes())
    assert set(RELEASE_ACTIONS) == set(schema["properties"]["code"]["enum"])
    actions = {release_projection(code).next_action for code in RELEASE_ACTIONS}
    for context, action in (("batch", "REDUCE_BATCH_SIZE"), ("artifact", "CORRECT_ARTIFACT")):
        projection = release_projection("INPUT_LIMIT_EXCEEDED", context)
        assert projection.next_action == action
        actions.add(action)
    assert actions == set(schema["properties"]["next_action"]["enum"])
    assert {release_projection(code).exit_code for code in RELEASE_ACTIONS} == {
        schema["properties"]["exit_code"]["const"]
    }


def test_record_failure_kinds_keep_distinct_meanings() -> None:
    from underwrite.error_contract.registry import ERROR_IDENTITIES, RECORD_PROJECTIONS, FailureKind

    grouped = {
        kind: {
            code
            for code, identity in ERROR_IDENTITIES.items()
            if code in RECORD_PROJECTIONS and identity.kind is kind
        }
        for kind in FailureKind
    }
    assert grouped == {
        FailureKind.INPUT: {"INVALID_SPEC", "INVALID_RECORD"},
        FailureKind.STORAGE: {
            "INVALID_STORE_LAYOUT",
            "INSECURE_STORE",
            "STORE_NOT_FOUND",
            "RECORD_NOT_FOUND",
            "RECORD_EXISTS",
            "RECORD_CAPTURE_INCOMPLETE",
            "RECORD_NOT_PUBLISHED",
            "STORE_IO_ERROR",
            "RECORD_FILE_NOT_FOUND",
        },
        FailureKind.SOURCE: {"SOURCE_READ_FAILED"},
        FailureKind.LIMIT: {
            "INPUT_LIMIT_EXCEEDED",
            "RECORD_LIMIT_EXCEEDED",
            "OUTPUT_LIMIT_EXCEEDED",
        },
        FailureKind.UNSUPPORTED: {"UNSUPPORTED_PLATFORM", "UNSUPPORTED_RECORD_CONTENT"},
        FailureKind.USAGE: {"USAGE_ERROR"},
        FailureKind.CONFIGURATION: {"SCHEMA_CONFIGURATION_ERROR"},
        FailureKind.INTERNAL: {"INTERNAL_ERROR"},
        FailureKind.INTEGRITY: {"RECORD_FILE_INTEGRITY_MISMATCH"},
    }


def test_unknown_record_code_or_context_is_refused() -> None:
    from underwrite.error_contract.registry import (
        ErrorContext,
        UnknownErrorProjection,
        acceptance_projection,
        evidence_projection,
        instrument_exit_code,
        measurement_projection,
        record_projection,
    )

    for code, context in (
        ("PRIVATE\nCODE", ErrorContext.RECORD),
        (cast(str, ["PRIVATE\nCODE"]), ErrorContext.RECORD),
        ("OUTPUT_LIMIT_EXCEEDED", cast(ErrorContext, "PRIVATE\nCONTEXT")),
    ):
        with pytest.raises(UnknownErrorProjection) as caught:
            record_projection(code, context)
        assert str(caught.value) == "UNKNOWN_ERROR_PROJECTION"
    with pytest.raises(UnknownErrorProjection) as caught:
        measurement_projection(cast(str, ["PRIVATE\nCODE"]))
    assert str(caught.value) == "UNKNOWN_ERROR_PROJECTION"
    with pytest.raises(UnknownErrorProjection) as caught:
        evidence_projection(cast(str, ["PRIVATE\nCODE"]))
    assert str(caught.value) == "UNKNOWN_ERROR_PROJECTION"
    for resolve in (
        lambda: instrument_exit_code("PRIVATE\nCODE"),
        lambda: acceptance_projection("PRIVATE\nCODE", "/candidate"),
        lambda: acceptance_projection("INVALID_READ", "/candidate"),
        lambda: evidence_projection("SOURCE_READ_FAILED", "PRIVATE\nCONTEXT"),
    ):
        with pytest.raises(UnknownErrorProjection) as caught:
            resolve()
        assert str(caught.value) == "UNKNOWN_ERROR_PROJECTION"


def test_evidence_verifier_context_is_the_only_action_override() -> None:
    from underwrite.error_contract.registry import (
        EVIDENCE_CONTEXT_ACTIONS,
        EVIDENCE_PROJECTIONS,
        evidence_projection,
    )

    assert set(EVIDENCE_CONTEXT_ACTIONS) == {("SOURCE_READ_FAILED", "verifier_executable")}
    default = EVIDENCE_PROJECTIONS["SOURCE_READ_FAILED"]
    override = evidence_projection("SOURCE_READ_FAILED", "verifier_executable")
    assert (default.next_action, override.next_action) == (
        "PROVIDE_REGULAR_LOCAL_FILE",
        "CORRECT_ARGUMENTS",
    )
    assert override.exit_code == default.exit_code


def test_public_registry_tables_are_immutable() -> None:
    from underwrite.error_contract.registry import (
        BASE_ACTIONS,
        ERROR_IDENTITIES,
        EVIDENCE_ACTIONS,
        EVIDENCE_PROJECTIONS,
        MEASUREMENT_PROJECTIONS,
        RECORD_PROJECTIONS,
    )

    for table, key in (
        (BASE_ACTIONS, "USAGE_ERROR"),
        (ERROR_IDENTITIES, "USAGE_ERROR"),
        (EVIDENCE_ACTIONS, "USAGE_ERROR"),
        (EVIDENCE_PROJECTIONS, "USAGE_ERROR"),
        (MEASUREMENT_PROJECTIONS, "USAGE_ERROR"),
        (RECORD_PROJECTIONS, "USAGE_ERROR"),
    ):
        with pytest.raises(TypeError):
            cast(dict[str, object], table)[key] = "PRIVATE_CALLER_VALUE"


def test_record_usage_error_keeps_exact_cli_bytes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from underwrite.cli.main import main; raise SystemExit(main())",
            "records",
            "list",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == RECORD_ERROR_EXIT
    assert result.stdout == b""
    assert result.stderr == (
        b'{"code":"USAGE_ERROR","exit_code":2,"location":"","next_action":"CORRECT_ARGUMENTS",'
        b'"reason":"INVALID_ARGUMENTS_SEE_HELP","retryable":false,"schema":"record_error.v1"}\n'
    )


def test_measurement_projection_matches_its_v1_envelope() -> None:
    from underwrite.cli.measurement_flow import MeasurementError
    from underwrite.error_contract.registry import (
        ERROR_IDENTITIES,
        MEASUREMENT_EXIT_CODE,
        MEASUREMENT_PROJECTIONS,
        FailureKind,
        measurement_projection,
    )

    schema = json.loads((ROOT / "contracts/measurement_error.v1.schema.json").read_bytes())
    expected = {
        "INVALID_OBSERVATION": "CORRECT_OBSERVATION",
        "INVALID_POLICY": "PROVIDE_COMPLETE_POLICY",
        "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
        "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "SOURCE_READ_FAILED": "PROVIDE_REGULAR_LOCAL_FILE",
        "SCHEMA_CONFIGURATION_ERROR": "REINSTALL_PACKAGE",
        "USAGE_ERROR": "CORRECT_ARGUMENTS",
        "UNSUPPORTED_PLATFORM": "USE_SUPPORTED_PLATFORM",
        "INTERNAL_ERROR": "REPORT_INTERNAL_ERROR",
    }
    assert {
        code: projection.next_action for code, projection in MEASUREMENT_PROJECTIONS.items()
    } == expected
    assert set(schema["properties"]["code"]["enum"]) == set(expected)
    assert set(schema["properties"]["next_action"]["enum"]) == set(expected.values())
    assert {projection.exit_code for projection in MEASUREMENT_PROJECTIONS.values()} == {
        RECORD_ERROR_EXIT
    }
    assert schema["properties"]["exit_code"]["const"] == MEASUREMENT_EXIT_CODE == RECORD_ERROR_EXIT
    assert all(
        measurement_projection(code) == projection
        for code, projection in MEASUREMENT_PROJECTIONS.items()
    )
    for code, projection in MEASUREMENT_PROJECTIONS.items():
        payload = MeasurementError(code, "TEST_REASON").payload
        assert payload["code"] == code
        assert payload["next_action"] == projection.next_action
        assert payload["exit_code"] == projection.exit_code
    assert ERROR_IDENTITIES["INVALID_OBSERVATION"].kind is FailureKind.INPUT
    assert ERROR_IDENTITIES["INVALID_POLICY"].kind is FailureKind.INPUT
    assert ERROR_IDENTITIES["UNSUPPORTED_PROFILE"].kind is FailureKind.UNSUPPORTED


def test_measurement_usage_error_keeps_exact_cli_bytes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from underwrite.cli.main import main; raise SystemExit(main())",
            "measure",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == RECORD_ERROR_EXIT
    assert result.stdout == b""
    assert result.stderr == (
        b'{"code":"USAGE_ERROR","exit_code":2,"location":"","next_action":"CORRECT_ARGUMENTS",'
        b'"reason":"INVALID_ARGUMENTS_SEE_HELP","retryable":false,"schema":"measurement_error.v1"}\n'
    )


def test_evidence_projection_matches_its_v1_envelope() -> None:
    from underwrite.cli.evidence import ERROR_ACTIONS, EvidenceError
    from underwrite.error_contract.registry import (
        ERROR_IDENTITIES,
        EVIDENCE_ACTIONS,
        EVIDENCE_PROJECTIONS,
        FailureKind,
    )

    schema = json.loads((ROOT / "contracts/evidence_error.v1.schema.json").read_bytes())
    expected = {
        "USAGE_ERROR": "CORRECT_ARGUMENTS",
        "INVALID_INPUT": "CORRECT_INPUT",
        "SOURCE_READ_FAILED": "PROVIDE_REGULAR_LOCAL_FILE",
        "INPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "OUTPUT_LIMIT_EXCEEDED": "REDUCE_INPUT_SIZE",
        "UNSUPPORTED_PROFILE": "USE_SUPPORTED_PROFILE",
        "UNSUPPORTED_FORMAT": "USE_SUPPORTED_PROFILE",
        "EXPECTED_HASH_MISMATCH": "CHECK_EXPECTED_HASH",
        "INVALID_MANIFEST": "CORRECT_MANIFEST",
        "INVALID_ARTIFACT_PATH": "CORRECT_MANIFEST",
        "SCHEMA_CONFIGURATION_ERROR": "REINSTALL_PACKAGE",
        "COMPUTATION_LIMIT_EXCEEDED": "REDUCE_COMPUTATION",
        "INTERNAL_ERROR": "REPORT_INTERNAL_ERROR",
        "UNSUPPORTED_PLATFORM": "USE_SUPPORTED_PLATFORM",
        "INPUT_BINDING_MISMATCH": "CORRECT_INPUT_BINDING",
    }
    assert {
        code: projection.next_action for code, projection in EVIDENCE_PROJECTIONS.items()
    } == expected
    assert ERROR_ACTIONS is EVIDENCE_ACTIONS
    assert set(schema["properties"]["code"]["enum"]) == set(expected)
    assert set(schema["properties"]["next_action"]["enum"]) == set(expected.values()) | {
        "CORRECT_ARTIFACT"
    }
    assert schema["properties"]["exit_code"]["const"] == RECORD_ERROR_EXIT
    for code, projection in EVIDENCE_PROJECTIONS.items():
        payload = EvidenceError(code, "TEST_REASON").payload
        assert (payload["code"], payload["next_action"], payload["exit_code"]) == (
            code,
            projection.next_action,
            projection.exit_code,
        )
    assert {
        code: ERROR_IDENTITIES[code].kind
        for code in (
            "INVALID_INPUT",
            "UNSUPPORTED_FORMAT",
            "EXPECTED_HASH_MISMATCH",
            "INVALID_MANIFEST",
            "INVALID_ARTIFACT_PATH",
            "COMPUTATION_LIMIT_EXCEEDED",
            "INPUT_BINDING_MISMATCH",
        )
    } == {
        "INVALID_INPUT": FailureKind.INPUT,
        "UNSUPPORTED_FORMAT": FailureKind.UNSUPPORTED,
        "EXPECTED_HASH_MISMATCH": FailureKind.INTEGRITY,
        "INVALID_MANIFEST": FailureKind.INPUT,
        "INVALID_ARTIFACT_PATH": FailureKind.INPUT,
        "COMPUTATION_LIMIT_EXCEEDED": FailureKind.LIMIT,
        "INPUT_BINDING_MISMATCH": FailureKind.INPUT,
    }


def test_evidence_usage_error_keeps_exact_cli_bytes() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from underwrite.cli.main import main; raise SystemExit(main())",
            "audit-promptfoo",
            "--json",
        ],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert result.returncode == RECORD_ERROR_EXIT
    assert result.stdout == b""
    assert result.stderr == (
        b'{"code":"USAGE_ERROR","exit_code":2,"location":"","next_action":"CORRECT_ARGUMENTS",'
        b'"reason":"INVALID_ARGUMENTS_SEE_HELP","retryable":false,"schema":"evidence_error.v1"}\n'
    )

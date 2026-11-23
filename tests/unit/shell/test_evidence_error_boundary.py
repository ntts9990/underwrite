"""Agent-facing evidence errors keep behavior fields bounded and structural."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest
from underwrite_core.canonical import canonical_bytes

from underwrite.cli import audit_deepeval, audit_promptfoo
from underwrite.cli.evidence import EvidenceError, output_limit_error, run_cli


def test_existing_finite_error_keeps_exact_payload_bytes() -> None:
    error = EvidenceError("INVALID_INPUT", "INVALID_JSON_INPUT", "/result/items/0")
    assert canonical_bytes(error.payload) == (
        b'{"code":"INVALID_INPUT","exit_code":2,"location":"/result/items/0",'
        b'"next_action":"CORRECT_INPUT","reason":"INVALID_JSON_INPUT",'
        b'"retryable":false,"schema":"evidence_error.v1"}'
    )


def test_legacy_trajectory_location_keeps_existing_cli_bytes() -> None:
    error = EvidenceError("UNSUPPORTED_FORMAT", "TRAJECTORY_UNSUPPORTED_VALUE", "trajectory")
    assert canonical_bytes(error.payload) == (
        b'{"code":"UNSUPPORTED_FORMAT","exit_code":2,"location":"trajectory",'
        b'"next_action":"USE_SUPPORTED_PROFILE","reason":"TRAJECTORY_UNSUPPORTED_VALUE",'
        b'"retryable":false,"schema":"evidence_error.v1"}'
    )


def test_output_limit_owner_keeps_exact_evidence_error_bytes() -> None:
    error = output_limit_error()
    assert canonical_bytes(error.payload) == (
        b'{"code":"OUTPUT_LIMIT_EXCEEDED","exit_code":2,"location":"",'
        b'"next_action":"REDUCE_INPUT_SIZE","reason":"OUTPUT_LIMIT_EXCEEDED",'
        b'"retryable":false,"schema":"evidence_error.v1"}'
    )


@pytest.mark.parametrize(
    ("reason", "location"),
    [
        ("BAD: user text", "/result"),
        ("PRIVATE\nINSTRUCTION", "/result"),
        ("A" * 129, "/result"),
        ("INVALID_JSON_INPUT", "/name with space"),
        ("INVALID_JSON_INPUT", "/a:b"),
        ("INVALID_JSON_INPUT", "/result/\x1bfield"),
        ("INVALID_JSON_INPUT", "/" + "a" * 513),
    ],
)
def test_untrusted_behavior_field_is_replaced_by_fixed_internal_error(
    reason: str, location: str
) -> None:
    error = EvidenceError("INVALID_INPUT", reason, location)
    assert error.payload == {
        "schema": "evidence_error.v1",
        "code": "INTERNAL_ERROR",
        "reason": "INTERNAL_ERROR",
        "location": "",
        "next_action": "REPORT_INTERNAL_ERROR",
        "retryable": False,
        "exit_code": 2,
    }
    encoded = canonical_bytes(error.payload).decode()
    assert reason not in encoded and location not in encoded


@pytest.mark.parametrize(
    ("code", "context"),
    [("PRIVATE_CANARY", "default"), ("INVALID_INPUT", "PRIVATE_CANARY")],
)
def test_unknown_projection_emits_only_fixed_internal_error(
    code: str, context: str, capsys: pytest.CaptureFixture[str]
) -> None:
    def configure(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--json", action="store_true")

    def produce(_args: argparse.Namespace) -> dict[str, object]:
        raise EvidenceError(code, "SAFE_REASON", "/result", context)

    exit_code = run_cli("boundary-probe", ["--json"], configure, produce, "unused.v1")
    captured = capsys.readouterr()
    internal = EvidenceError("INTERNAL_ERROR", "INTERNAL_ERROR")
    expected = canonical_bytes(internal.payload).decode()
    assert exit_code == internal.exit_code
    assert captured.out == ""
    assert captured.err == expected + "\n"
    assert "PRIVATE_CANARY" not in captured.err


def test_legacy_root_input_errors_keep_exact_cli_bytes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "array.json"
    source.write_bytes(b"[]")
    for command, reason in (
        (audit_promptfoo.main, "PROMPTFOO_OUTPUT_OBJECT_REQUIRED"),
        (audit_deepeval.main, "DEEPEVAL_OBJECT_REQUIRED"),
    ):
        code = command([str(source), "--json"])
        streams = capsys.readouterr()
        expected = canonical_bytes(
            {
                "schema": "evidence_error.v1",
                "code": "INVALID_INPUT",
                "reason": reason,
                "location": "/",
                "next_action": "CORRECT_INPUT",
                "retryable": False,
                "exit_code": EvidenceError("INVALID_INPUT", "TEST_REASON").exit_code,
            }
        )
        assert code == EvidenceError("INVALID_INPUT", "TEST_REASON").exit_code
        assert streams.out == ""
        assert streams.err.encode() == expected + b"\n"

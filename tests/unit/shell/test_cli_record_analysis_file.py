"""Captured analysis members preserve their bytes, role, and refusal boundary."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator
from underwrite_core.canonical import CanonicalizationError, canonical_bytes

from underwrite.cli.evidence import EvidenceError
from underwrite.cli.record_capture import analysis_record_file

RESULT = Draft202012Validator({"properties": {"kind": {"const": "result"}}, "required": ["kind"]})
ERROR = Draft202012Validator({"properties": {"kind": {"const": "error"}}, "required": ["kind"]})


@pytest.mark.parametrize(
    ("state", "key", "kind", "name", "role"),
    [
        ("result", "report", "result", "analysis/custom.json", "result"),
        ("error", "error", "error", "analysis/error.json", "log"),
    ],
)
def test_analysis_member_exact_bytes_role_and_inclusive_cap(
    state: str, key: str, kind: str, name: str, role: str
) -> None:
    payload = {"kind": kind, "count": 0}
    analysis = {"state": state, key: payload}
    encoded = canonical_bytes(payload) + b"\n"
    member = analysis_record_file(
        analysis,
        result_name="analysis/custom.json",
        result_validator=RESULT,
        error_validator=ERROR,
        max_bytes=len(encoded),
    )
    assert (member.name, member.role, member.data, member.media_type) == (
        name,
        role,
        encoded,
        "application/json",
    )
    with pytest.raises(EvidenceError) as rejected:
        analysis_record_file(
            analysis,
            result_name="analysis/custom.json",
            result_validator=RESULT,
            error_validator=ERROR,
            max_bytes=len(encoded) - 1,
        )
    assert rejected.value.payload["code"] == "OUTPUT_LIMIT_EXCEEDED"


@pytest.mark.parametrize(("state", "key"), [("result", "report"), ("error", "error")])
def test_analysis_member_uses_the_matching_validator(state: str, key: str) -> None:
    with pytest.raises(EvidenceError) as rejected:
        analysis_record_file(
            {"state": state, key: {"kind": "wrong"}},
            result_name="analysis/custom.json",
            result_validator=RESULT,
            error_validator=ERROR,
            max_bytes=100,
        )
    assert rejected.value.payload["reason"] == "SCHEMA_VALIDATION_FAILED"


def test_analysis_member_keeps_canonicalization_refusal_for_caller() -> None:
    with pytest.raises(CanonicalizationError, match="NON_FINITE_NUMBER"):
        analysis_record_file(
            {"state": "result", "report": {"kind": "result", "value": float("nan")}},
            result_name="analysis/custom.json",
            result_validator=RESULT,
            error_validator=ERROR,
            max_bytes=100,
        )

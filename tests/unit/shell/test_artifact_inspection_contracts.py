"""C1 contracts reject contradictory findings before runtime integration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

ROOT = Path(__file__).resolve().parents[3]
# The packaged path, so a contract dropped from the package fails here too.
PACKAGED = ROOT / "src/underwrite/_contracts"
HASH = "sha256:" + "a" * 64
LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "RELEASE_BINDING_NOT_CHECKED",
    "MEASUREMENT_NOT_PERFORMED",
    "NO_DEPLOYMENT_DECISION",
]


def validate(name: str, payload: dict[str, Any]) -> None:
    schema = json.loads((PACKAGED / f"{name}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(payload)  # pyright: ignore[reportUnknownMemberType]


def report() -> dict[str, Any]:
    return {
        "schema": "artifact_inspection.v1",
        "manifest_hash": HASH,
        "raw_hash": HASH,
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
        "observation": {"kind": "eval_run", "digest": HASH},
        "projection": "created",
        "expected_hash_check": "not_supplied",
        "diagnostics": [],
        "limitations": list(LIMITATIONS),
    }


def diagnostic(mismatch: bool = False) -> dict[str, Any]:
    return {
        "code": "EXPECTED_HASH_MISMATCH" if mismatch else "INVALID_INPUT",
        "reason": "EXPECTED_HASH_MISMATCH" if mismatch else "INVALID_JSON",
        "location": "/artifact/expected_hash" if mismatch else "/artifact",
        "next_action": "CHECK_EXPECTED_HASH" if mismatch else "CORRECT_ARTIFACT",
        "retryable": False,
    }


@pytest.mark.parametrize("expected", ["matched", "not_supplied", "mismatched"])
def test_completed_reports(expected: str) -> None:
    body = report()
    body["expected_hash_check"] = expected
    if expected == "mismatched":
        body.update(projection="not_created", observation=None, diagnostics=[diagnostic(True)])
    validate("artifact_inspection", body)
    body.update(
        projection="not_created",
        observation=None,
        diagnostics=[diagnostic(expected == "mismatched")],
    )
    validate("artifact_inspection", body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("observation", None),
        ("projection", "not_created"),
        ("expected_hash_check", "mismatched"),
        ("diagnostics", [diagnostic()]),
        ("limitations", LIMITATIONS[:-1]),
        ("limitations", LIMITATIONS + [LIMITATIONS[0]]),
        ("limitations", [LIMITATIONS[0]] * 4),
        ("limitations", LIMITATIONS[:-1] + ["VERIFIED"]),
        ("manifest_hash", HASH + "\n"),
        ("raw_hash", HASH + "\n"),
        ("payload", {"secret": "hidden"}),
        ("exit_code", 0),
        ("observation", {"kind": "eval_run", "digest": HASH + "\n"}),
        ("source", {"format": "x", "format_version": "1", "ref": "secret"}),
    ],
)
def test_invalid_completed_reports(field: str, value: Any) -> None:
    body = report()
    body[field] = value
    with pytest.raises(ValidationError):
        validate("artifact_inspection", body)


@pytest.mark.parametrize(
    "change",
    [
        {"expected_hash_check": "mismatched"},
        {"observation": {"kind": "eval_run", "digest": HASH}},
        {"diagnostics": []},
        {"diagnostics": [diagnostic(True)]},
        {"diagnostics": [{**diagnostic(), "reason": "INVALID_JSON\n"}]},
        {"diagnostics": [{**diagnostic(), "location": "/artifact\n"}]},
        {"diagnostics": [{**diagnostic(), "next_action": "CHECK_EXPECTED_HASH"}]},
    ],
)
def test_refusal_cannot_contradict_hash_or_projection(change: dict[str, Any]) -> None:
    body = report()
    body.update(projection="not_created", observation=None, diagnostics=[diagnostic()])
    body.update(change)
    with pytest.raises(ValidationError):
        validate("artifact_inspection", body)


@pytest.mark.parametrize(
    "diagnostics",
    [
        [diagnostic(True), diagnostic()],
        [diagnostic(True), {**diagnostic(), "code": "INVALID_OBSERVATION"}],
        [diagnostic(True), diagnostic(True)],
        [{**diagnostic(True), "reason": "INVALID_JSON"}],
        [{**diagnostic(True), "location": "/artifact"}],
        [{**diagnostic(True), "next_action": "CORRECT_ARTIFACT"}],
        [{**diagnostic(True), "retryable": True}],
    ],
)
def test_mismatch_reports_only_the_check_actually_performed(
    diagnostics: list[dict[str, Any]],
) -> None:
    body = report()
    body.update(
        projection="not_created",
        observation=None,
        expected_hash_check="mismatched",
        diagnostics=diagnostics,
    )
    with pytest.raises(ValidationError):
        validate("artifact_inspection", body)


def test_non_hash_refusal_can_have_multiple_concrete_findings() -> None:
    body = report()
    body.update(
        projection="not_created",
        observation=None,
        diagnostics=[diagnostic(), {**diagnostic(), "code": "INVALID_OBSERVATION"}],
    )
    validate("artifact_inspection", body)


def manifest() -> dict[str, Any]:
    return {
        "schema": "artifact_case.v1",
        "artifact": {"path": "data/input.json"},
        "source": {"format": "underwrite.evidence-bundle", "format_version": "v1"},
    }


@pytest.mark.parametrize("path", ["input.json", "sub/input.json", "a\n/b", ".hidden/x"])
def test_manifest_paths(path: str) -> None:
    body = manifest()
    body["artifact"]["path"] = path
    body["artifact"]["expected_hash"] = HASH
    validate("artifact_case", body)


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs",
        "a//b",
        "a/",
        ".",
        "..",
        "a/../b",
        "./b",
        "a/.",
        "a\n/../b",
        "a\n/..",
        "a\\b",
        "file:b",
        "a\0b",
        "a" * 1025,
    ],
)
def test_manifest_unsafe_paths(path: str) -> None:
    body = manifest()
    body["artifact"]["path"] = path
    with pytest.raises(ValidationError):
        validate("artifact_case", body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("ref", None),
        ("locator", None),
        ("ref", ""),
        ("format", ""),
        ("format_version", "a" * 129),
        ("locator", "a" * 1025),
        ("extra", True),
    ],
)
def test_manifest_source_bounds(field: str, value: Any) -> None:
    body = manifest()
    body["source"][field] = value
    with pytest.raises(ValidationError):
        validate("artifact_case", body)


def test_manifest_hash_has_exact_end() -> None:
    body = manifest()
    body["artifact"]["expected_hash"] = HASH + "\n"
    with pytest.raises(ValidationError):
        validate("artifact_case", body)


def test_error_is_not_completed_report() -> None:
    body = {
        "schema": "artifact_inspection_error.v1",
        "code": "INVALID_MANIFEST",
        "reason": "INVALID_JSON",
        "location": "",
        "next_action": "CORRECT_MANIFEST",
        "retryable": False,
        "exit_code": 2,
    }
    validate("artifact_inspection_error", body)
    with pytest.raises(ValidationError):
        validate("artifact_inspection", body)
    for field, value in [
        ("exit_code", 1),
        ("raw_hash", HASH),
        ("reason", "INVALID_JSON\n"),
        ("location", "\n"),
        ("next_action", "RUN_SHELL"),
    ]:
        with pytest.raises(ValidationError):
            validate("artifact_inspection_error", {**body, field: value})


@pytest.mark.parametrize(
    "name,body",
    [
        ("artifact_case", manifest()),
        ("artifact_inspection", report()),
    ],
)
def test_all_required_fields_and_closed_objects(name: str, body: dict[str, Any]) -> None:
    for field in body:
        with pytest.raises(ValidationError):
            validate(name, {key: value for key, value in body.items() if key != field})
    with pytest.raises(ValidationError):
        validate(name, {**body, "unexpected": True})

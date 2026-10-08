"""C3 schema shape checks, distinct from composite and relational acceptance."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError

from underwrite.instrument.ingest import release
from underwrite.instrument.ingest.release_bindings import FIELDS, MAX_IDENTITY_LENGTH

ROOT = Path(__file__).resolve().parents[3]
# The packaged path, so a contract dropped from the package fails here too.
PACKAGED = ROOT / "src/underwrite/_contracts"
HASH = "sha256:" + "a" * 64
LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "RELEASE_AUTHENTICITY_UNVERIFIED",
    "RUN_INDEPENDENCE_UNVERIFIED",
    "CONTENT_ADEQUACY_NOT_ASSESSED",
    "MEASUREMENT_NOT_PERFORMED",
    "NO_DEPLOYMENT_DECISION",
]


def _defs(name: str) -> dict[str, Any]:
    schema = json.loads((PACKAGED / f"{name}.v1.schema.json").read_text())
    return cast("dict[str, Any]", schema["$defs"])


def validate(name: str, body: dict[str, Any]) -> None:
    schema = json.loads((PACKAGED / f"{name}.v1.schema.json").read_text())
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(body)  # pyright: ignore[reportUnknownMemberType]


def test_diagnostic_enum_covers_every_reason_the_rechecker_derives() -> None:
    """A new DIAGNOSTICS reason must reach the schema, or `check` fails as INTERNAL_ERROR."""
    enum = set(_defs("release_inspection")["diagnostic"]["properties"]["code"]["enum"])
    assert release.READ_REASONS | release.BUFFER_REASONS <= enum


def test_release_identity_defs_agree_with_each_other_and_the_code() -> None:
    """The two C3 schemas copy these $defs (no non-local $ref); the code holds the rest."""
    case, inspection = _defs("release_case"), _defs("release_inspection")
    for name in ("identity", "id", "subject"):
        assert case[name] == inspection[name], name
    assert tuple(case["subject"]["required"]) == FIELDS
    assert case["identity"]["maxLength"] == MAX_IDENTITY_LENGTH


def manifest() -> dict[str, Any]:
    return {
        "schema": "release_case.v1",
        "subject": dict.fromkeys(FIELDS, "demo"),
        "requirements": [
            {"id": "evaluation", "format": "deepeval.test-run", "format_version": "4.1.1"}
        ],
        "evidence": [{"id": "eval", "requirement": "evaluation", "case": {}}],
    }


def report() -> dict[str, Any]:
    return {
        "schema": "release_inspection.v1",
        "manifest_hash": HASH,
        "scope": "declared_release_linkage",
        "result": "requirements_matched",
        "subject": dict.fromkeys(FIELDS, "demo"),
        "requirements": [
            {"id": "evaluation", "result": "matched", "evidence_ids": ["eval"], "diagnostics": []}
        ],
        "evidence": [
            {
                "id": "eval",
                "requirement": "evaluation",
                "location": "/evidence/0",
                "artifact": {
                    "raw_hash": HASH,
                    "observation": {"kind": "eval_run", "digest": HASH},
                    "projection": "created",
                    "expected_hash_check": "not_supplied",
                },
                "record_presence": "present",
                "bindings": {
                    field: {
                        "coverage": "unmapped",
                        "origin": "declared",
                        "state": "matched",
                        "source_value": None,
                        "declared_value": "demo",
                    }
                    for field in FIELDS
                },
                "diagnostics": [],
            }
        ],
        "limitations": LIMITATIONS[:],
    }


def test_envelope_is_explicitly_incomplete() -> None:
    validate("release_case", manifest())
    schema = json.loads((ROOT / "contracts/release_case.v1.schema.json").read_text())
    assert "schema-alone validation is incomplete" in json.dumps(schema)
    assert "artifact_case.v1" in json.dumps(schema)


@pytest.mark.parametrize(
    "field,value",
    [
        ("requirements", []),
        ("extra", True),
        ("subject", {**dict.fromkeys(FIELDS, "demo"), "run_key": "x"}),
        ("evidence", [{"id": "eval\n", "requirement": "evaluation", "case": dict[str, Any]()}]),
    ],
)
def test_invalid_envelope(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        validate("release_case", {**manifest(), field: value})


@pytest.mark.parametrize("value", [None, True, "", " ", "a" * 129, "a\n", "\ud800"])
def test_subject_shape_rejects_unusable_strings(value: Any) -> None:
    body = manifest()
    body["subject"]["target"] = value
    with pytest.raises(ValidationError):
        validate("release_case", body)


def test_positive_report_and_failed_read_shapes() -> None:
    body = report()
    validate("release_inspection", body)
    body["result"] = "gaps_found"
    body["requirements"][0]["result"] = "unresolved"
    row = body["evidence"][0]
    row["artifact"].update(
        raw_hash=None, observation=None, projection="not_created", expected_hash_check="not_checked"
    )
    row["record_presence"] = "unknown"
    for binding in row["bindings"].values():
        binding.update(coverage="unavailable", origin="unknown", state="unknown")
    validate("release_inspection", body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_hash", HASH + "\n"),
        ("limitations", LIMITATIONS[:-1]),
        ("limitations", [LIMITATIONS[0]] * 6),
        ("scope", "approved"),
        ("payload", dict[str, Any]()),
    ],
)
def test_invalid_report(field: str, value: Any) -> None:
    with pytest.raises(ValidationError):
        validate("release_inspection", {**report(), field: value})


@pytest.mark.parametrize(
    "change",
    [
        {"observation": None},
        {"raw_hash": None},
        {"expected_hash_check": "not_checked"},
        {"expected_hash_check": "mismatched"},
        {"raw_hash": HASH + "\n"},
        {"ref": "secret"},
    ],
)
def test_artifact_invariants(change: dict[str, Any]) -> None:
    body = report()
    body["evidence"][0]["artifact"].update(change)
    with pytest.raises(ValidationError):
        validate("release_inspection", body)


@pytest.mark.parametrize(
    "change",
    [
        {"origin": "source_present"},
        {"source_value": "x"},
        {"declared_value": None},
        {"coverage": "complete"},
        {"state": "ambiguous"},
        {"extra": True},
    ],
)
def test_binding_shape_invariants(change: dict[str, Any]) -> None:
    body = report()
    body["evidence"][0]["bindings"]["target"].update(change)
    with pytest.raises(ValidationError):
        validate("release_inspection", body)


def test_error_closed_safe_shape() -> None:
    body = {
        "schema": "release_inspection_error.v1",
        "code": "OUTPUT_LIMIT_EXCEEDED",
        "reason": "OUTPUT_LIMIT_EXCEEDED",
        "location": "",
        "next_action": "REDUCE_BATCH_SIZE",
        "retryable": False,
        "exit_code": 2,
    }
    validate("release_inspection_error", body)
    for key, value in [
        ("retryable", True),
        ("reason", "arbitrary secret"),
        ("location", "\n"),
        ("exit_code", 1),
        ("payload", dict[str, Any]()),
    ]:
        with pytest.raises(ValidationError):
            validate("release_inspection_error", {**body, key: value})


def test_failed_projection_cannot_claim_record_presence() -> None:
    body = report()
    body["evidence"][0]["artifact"].update(
        observation=None,
        projection="not_created",
        raw_hash=None,
        expected_hash_check="not_checked",
    )
    with pytest.raises(ValidationError):
        validate("release_inspection", body)


@pytest.mark.parametrize(
    "field,value",
    [
        ("reason", "INVALID_JSON_INPUT"),
        ("retryable", False),
        ("code", "SECRET"),
        ("code", "IDENTITY_UNKNOWN\n"),
        ("source_path", "/private/secret"),
        ("location", "/evidence/0\n"),
        ("location", "/home/secret"),
        ("field", "run_key"),
        ("next_action", "RUN_COMMAND"),
    ],
)
def test_diagnostic_is_closed_and_allowlisted(field: str, value: Any) -> None:
    body = report()
    item = {
        "code": "IDENTITY_UNKNOWN",
        "location": "/evidence/0",
        "field": "version",
        "source_path": "/observations/*/release",
        "next_action": "PROVIDE_BINDING_METADATA",
    }
    body["evidence"][0]["diagnostics"] = [item]
    validate("release_inspection", body)
    item[field] = value
    with pytest.raises(ValidationError):
        validate("release_inspection", body)


@pytest.mark.parametrize(
    "coverage,origin,state,source,declared",
    [
        ("unavailable", "unknown", "unknown", None, "demo"),
        ("unmapped", "unknown", "unknown", None, None),
        ("absent", "declared", "matched", None, "demo"),
        ("complete", "source_present", "matched", "demo", None),
        ("partial", "source_present", "unknown", "demo", None),
        ("partial", "source_present", "conflict", None, "demo"),
        ("unusable", "source_present", "unknown", "demo", "demo"),
        ("unusable", "unknown", "unknown", None, "demo"),
    ],
)
def test_coverage_table_shape(
    coverage: str,
    origin: str,
    state: str,
    source: str | None,
    declared: str | None,
) -> None:
    body = report()
    body["evidence"][0]["bindings"]["version"] = {
        "coverage": coverage,
        "origin": origin,
        "state": state,
        "source_value": source,
        "declared_value": declared,
    }
    validate("release_inspection", body)


def test_schema_references_are_local_and_all_required_output_fields_exist() -> None:
    for name in ("release_case", "release_inspection", "release_inspection_error"):
        schema = json.loads((PACKAGED / f"{name}.v1.schema.json").read_text())
        pending: list[Any] = [schema]
        while pending:
            node = pending.pop()
            if isinstance(node, dict):
                mapping = cast(dict[str, Any], node)
                if "$ref" in mapping:
                    assert str(mapping["$ref"]).startswith("#/")
                pending.extend(mapping.values())
            elif isinstance(node, list):
                pending.extend(cast(list[Any], node))
    body = report()
    for key in body:
        with pytest.raises(ValidationError):
            validate("release_inspection", {k: v for k, v in body.items() if k != key})
    del body["evidence"][0]["bindings"]["conditions"]
    with pytest.raises(ValidationError):
        validate("release_inspection", body)

"""Record contracts are standalone, bounded, generated where declared, and semantically narrow."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _repo_paths import repo_root
from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from underwrite_core.canonical import content_digest

ROOT = repo_root(Path(__file__).resolve())
CONTRACTS = ROOT / "contracts"
FIXTURES = ROOT / "fixtures/examples/records"
NAMES = (
    "record_spec.v1",
    "result_record.v1",
    "record_view.v1",
    "record_document.v1",
    "record_catalog.v1",
    "record_verification.v1",
    "record_error.v1",
)
ROLES = {
    "source",
    "input",
    "policy",
    "result",
    "verification",
    "code",
    "log",
    "environment",
}


class Validator(Protocol):
    def validate(self, instance: object) -> None: ...


def schema(name: str) -> dict[str, Any]:
    return json.loads((CONTRACTS / f"{name}.schema.json").read_bytes())


def validator(name: str) -> Validator:
    return cast("Validator", Draft202012Validator(schema(name), format_checker=FormatChecker()))


def valid_spec() -> dict[str, Any]:
    return json.loads((FIXTURES / "record-spec.json").read_bytes())


def valid_record() -> dict[str, Any]:
    body = (FIXTURES / "note.txt").read_bytes()
    return {
        "schema": "result_record.v1",
        "record_id": "synthetic.capture-001",
        "kind": "source.capture",
        "label": "Synthetic source capture",
        "metadata": {"fixture": "true", "scope": "contract-only"},
        "selection": {"scope": "explicit_files", "excluded": None},
        "rerun_verification": {"status": "not_performed", "reference": None},
        "provenance": {"verified": False, "attestation_ref": None},
        "links": [],
        "captured_at": "2026-10-11T12:34:56Z",
        "files": [
            {
                "name": "source/note.txt",
                "role": "source",
                "sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "media_type": "text/plain;charset=utf-8",
                "source_locator": "synthetic://records/note",
            }
        ],
    }


def nodes(value: object, *, root: bool = True) -> Iterator[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        mapping = cast(Mapping[str, Any], value)
        yield mapping
        for item in mapping.values():
            yield from nodes(item, root=False)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            yield from nodes(item, root=False)


def test_all_record_contracts_are_valid_standalone_local_schemas() -> None:
    for name in NAMES:
        document = schema(name)
        Draft202012Validator.check_schema(document)
        assert document["$id"] == f"urn:underwrite:contract:{name}"
        for index, node in enumerate(nodes(document)):
            if "$ref" in node:
                assert cast(str, node["$ref"]).startswith("#/")
            if index:
                assert "$id" not in node


def test_unique_object_arrays_are_bounded_before_items_and_uniqueness() -> None:
    for name in NAMES:
        for node in nodes(schema(name)):
            if node.get("uniqueItems") is not True:
                continue
            keys = list(node)
            assert "maxItems" in node and "items" in node
            assert keys.index("maxItems") < keys.index("uniqueItems")
            assert keys.index("items") < keys.index("uniqueItems")


def test_synthetic_spec_fixture_selects_existing_bytes_and_validates() -> None:
    spec = valid_spec()
    validator("record_spec.v1").validate(spec)
    assert (FIXTURES / spec["files"][0]["path"]).read_text().startswith("Synthetic record")


@pytest.mark.parametrize("axis", ["selection", "rerun_verification", "provenance"])
def test_scope_verification_and_provenance_axes_are_required(axis: str) -> None:
    spec = valid_spec()
    del spec[axis]
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)
    record = valid_record()
    del record[axis]
    with pytest.raises(ValidationError):
        validator("result_record.v1").validate(record)


def test_selection_axis_is_bounded_and_does_not_claim_directory_completeness() -> None:
    spec = valid_spec()
    spec["selection"] = {"scope": "explicit_files", "excluded": ["cache", "private raw"]}
    validator("record_spec.v1").validate(spec)
    for excluded in (
        ["same", "same"],
        [f"excluded-{index}" for index in range(33)],
    ):
        invalid = copy.deepcopy(spec)
        invalid["selection"]["excluded"] = excluded
        with pytest.raises(ValidationError):
            validator("record_spec.v1").validate(invalid)
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(
            {**spec, "selection": {"scope": "directory_complete", "excluded": None}}
        )


def test_artifact_references_are_structured_declarations_with_safe_default_states() -> None:
    spec = valid_spec()
    assert spec["rerun_verification"] == {"status": "not_performed", "reference": None}
    assert spec["provenance"] == {"verified": False, "attestation_ref": None}
    file_spec = spec["files"][0]
    spec["files"].append(
        {
            **file_spec,
            "name": "verification/replay.json",
            "role": "verification",
        }
    )
    spec["rerun_verification"] = {
        "status": "referenced",
        "reference": {"scope": "this_record", "name": "verification/replay.json"},
    }
    spec["links"] = [
        {
            "relation": "input",
            "record_id": "synthetic.input-001",
            "manifest_digest": "sha256:" + "a" * 64,
        }
    ]
    spec["provenance"] = {
        "verified": False,
        "attestation_ref": {
            "scope": "linked_record",
            "record_id": "synthetic.input-001",
            "manifest_digest": "sha256:" + "a" * 64,
            "name": "verification/attestation.json",
        },
    }
    validator("record_spec.v1").validate(spec)
    # Existence, verification role, and exact direct-link matching are P2 runtime checks.
    mismatch = copy.deepcopy(spec)
    mismatch["provenance"]["attestation_ref"]["manifest_digest"] = "sha256:" + "b" * 64
    validator("record_spec.v1").validate(mismatch)


@pytest.mark.parametrize(
    "axis",
    ["not_performed_with_ref", "referenced_without_ref", "verified_true", "bad_this", "bad_link"],
)
def test_artifact_axes_reject_structurally_contradictory_states(axis: str) -> None:
    spec = valid_spec()
    this_ref = {"scope": "this_record", "name": "source/note.txt"}
    if axis == "not_performed_with_ref":
        spec["rerun_verification"] = {"status": "not_performed", "reference": this_ref}
    elif axis == "referenced_without_ref":
        spec["rerun_verification"] = {"status": "referenced", "reference": None}
    elif axis == "verified_true":
        spec["provenance"] = {"verified": True, "attestation_ref": None}
    elif axis == "bad_this":
        spec["provenance"] = {
            "verified": False,
            "attestation_ref": {**this_ref, "record_id": "extra"},
        }
    else:
        spec["provenance"] = {
            "verified": False,
            "attestation_ref": {
                "scope": "linked_record",
                "record_id": "linked",
                "name": "verification/a.json",
            },
        }
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "/absolute",
        "./dot",
        "dot/../escape",
        "double//component",
        "trailing/",
        "back\\slash",
        "nul\u0000name",
        ".",
        "..",
    ],
)
def test_logical_names_reject_nonportable_single_path_shapes(name: str) -> None:
    spec = valid_spec()
    spec["files"][0]["name"] = name
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("record_id", "valid-id\n"),
        ("kind", "valid.kind\n"),
    ],
)
def test_identifier_patterns_reject_final_newlines(field: str, value: str) -> None:
    spec = valid_spec()
    spec[field] = value
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)
    spec = valid_spec()
    spec["files"][0]["name"] = "source/valid.txt\n"
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)


@pytest.mark.parametrize(
    "names",
    [
        ("Source/A.txt", "source/a.txt"),
        ("source", "source/nested.txt"),
    ],
)
def test_cross_name_casefold_and_prefix_conflicts_remain_runtime_checks(
    names: tuple[str, str],
) -> None:
    spec = valid_spec()
    first = spec["files"][0]
    spec["files"] = [
        {**first, "name": name, "path": f"input-{index}.txt"} for index, name in enumerate(names)
    ]
    validator("record_spec.v1").validate(spec)


def test_spec_and_manifest_keep_source_path_separate() -> None:
    spec = valid_spec()
    spec["files"][0]["path"] = "/operator/selected/source.txt"
    validator("record_spec.v1").validate(spec)
    record = valid_record()
    assert "path" not in record["files"][0]
    with pytest.raises(ValidationError):
        validator("result_record.v1").validate(
            {**record, "files": [{**record["files"][0], "path": "private"}]}
        )
    spec["files"][0]["path"] = "source\u0000tail"
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)


def test_links_are_direct_dependencies_or_related_records_without_extra_semantics() -> None:
    record = valid_record()
    link = {
        "relation": "input",
        "record_id": "synthetic.input-001",
        "manifest_digest": "sha256:" + "a" * 64,
    }
    record["links"] = [link]
    validator("result_record.v1").validate(record)
    for relation in ("supersedes", "provenance", "approval"):
        invalid = copy.deepcopy(record)
        invalid["links"][0]["relation"] = relation
        with pytest.raises(ValidationError):
            validator("result_record.v1").validate(invalid)
    with pytest.raises(ValidationError):
        validator("result_record.v1").validate(
            {**record, "manifest_digest": content_digest(record)}
        )
    # One relation per record_id is a P2 semantic check, beyond exact-object uniqueness.
    record["links"].append({**link, "relation": "related", "manifest_digest": "sha256:" + "b" * 64})
    validator("result_record.v1").validate(record)


@pytest.mark.parametrize("fault", ["digest", "media_type", "captured_at"])
def test_manifest_anchored_values_reject_final_newlines(fault: str) -> None:
    record = valid_record()
    if fault == "captured_at":
        record["captured_at"] += "\n"
    else:
        field = "sha256" if fault == "digest" else fault
        record["files"][0][field] += "\n"
    with pytest.raises(ValidationError):
        validator("result_record.v1").validate(record)


def test_record_identity_kind_roles_and_collection_bounds() -> None:
    spec = valid_spec()
    record_id = "a" + "b" * 95
    kind = "a" + "b" * 63
    spec.update(record_id=record_id, kind=kind)
    validator("record_spec.v1").validate(spec)
    assert set(schema("record_spec.v1")["$defs"]["role"]["enum"]) == ROLES
    invalid_identities = (
        ("record_id", "A"),
        ("record_id", "a" * 97),
        ("kind", "A"),
        ("kind", "a" * 65),
    )
    for field, value in invalid_identities:
        invalid = {**spec, field: value}
        with pytest.raises(ValidationError):
            validator("record_spec.v1").validate(invalid)
    for role in ROLES:
        changed = copy.deepcopy(spec)
        changed["files"][0]["role"] = role
        validator("record_spec.v1").validate(changed)


def test_spec_bounds_and_stored_file_size_are_finite() -> None:
    spec = valid_spec()
    file_spec = spec["files"][0]
    spec["files"] = [{**file_spec, "name": f"source/{index}.txt"} for index in range(65)]
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)
    spec = valid_spec()
    spec["metadata"] = {f"key-{index}": "value" for index in range(17)}
    with pytest.raises(ValidationError):
        validator("record_spec.v1").validate(spec)
    record = valid_record()
    record["files"][0]["bytes"] = 134217729
    with pytest.raises(ValidationError):
        validator("result_record.v1").validate(record)


def test_generated_view_is_current_and_embeds_the_full_record_schema() -> None:
    subprocess.run(
        [sys.executable, str(ROOT / "scripts/build_record_view.py"), "--check"],
        cwd=ROOT,
        check=True,
    )
    result = schema("result_record.v1")
    view = schema("record_view.v1")
    expected_record = {
        key: value
        for key, value in result.items()
        if key not in {"$schema", "$id", "title", "$defs"}
    }
    assert view["$defs"]["result_record"] == expected_record
    copied_definitions = {
        key: value for key, value in view["$defs"].items() if key != "result_record"
    }
    assert copied_definitions == result["$defs"]


def test_view_validates_full_record_and_rejects_nested_manifest_faults() -> None:
    record = valid_record()
    view: dict[str, Any] = {
        "schema": "record_view.v1",
        "manifest_digest": content_digest(record),
        "record": record,
    }
    validator("record_view.v1").validate(view)
    for mutation in ("missing_id", "bad_digest", "source_path"):
        invalid = copy.deepcopy(view)
        if mutation == "missing_id":
            del invalid["record"]["record_id"]
        elif mutation == "bad_digest":
            invalid["record"]["files"][0]["sha256"] = "sha256:bad"
        else:
            invalid["record"]["files"][0]["path"] = "/private/source"
        with pytest.raises(ValidationError):
            validator("record_view.v1").validate(invalid)


def test_catalog_complete_and_unpublished_entries_are_exact_and_bounded() -> None:
    record = valid_record()
    complete = {
        "record_id": record["record_id"],
        "state": "complete",
        "manifest_digest": content_digest(record),
        "kind": record["kind"],
        "label": record["label"],
        "captured_at": record["captured_at"],
    }
    unpublished = {"record_id": "synthetic.pending-001", "state": "unpublished"}
    catalog: dict[str, Any] = {
        "schema": "record_catalog.v1",
        "record_count": 2,
        "records": [complete, unpublished],
    }
    validator("record_catalog.v1").validate(catalog)
    # Sorting and record_count equality depend on the complete scanned population in P2.
    validator("record_catalog.v1").validate(
        {**catalog, "record_count": 0, "records": list(reversed(catalog["records"]))}
    )
    for index in range(2):
        invalid = copy.deepcopy(catalog)
        invalid["records"][index]["extra"] = True
        with pytest.raises(ValidationError):
            validator("record_catalog.v1").validate(invalid)
    oversized = {
        "schema": "record_catalog.v1",
        "record_count": 1000,
        "records": [{"record_id": f"r-{index}", "state": "unpublished"} for index in range(1001)],
    }
    with pytest.raises(ValidationError):
        validator("record_catalog.v1").validate(oversized)


def _verification_with_finding(finding: dict[str, object]) -> dict[str, Any]:
    record = valid_record()
    return {
        "schema": "record_verification.v1",
        "record_id": record["record_id"],
        "manifest_digest": content_digest(record),
        "valid": False,
        "files_checked": 1,
        "links_checked": 0,
        "findings": [finding],
        "verification_scope": "stored_bytes_and_direct_links",
    }


def test_verification_false_is_typed_result_data_and_true_has_no_findings() -> None:
    record = valid_record()
    invalid: dict[str, Any] = {
        "schema": "record_verification.v1",
        "record_id": record["record_id"],
        "manifest_digest": content_digest(record),
        "valid": False,
        "files_checked": 1,
        "links_checked": 0,
        "findings": [
            {
                "code": "FILE_DIGEST_MISMATCH",
                "name": "source/note.txt",
                "record_id": None,
            }
        ],
        "verification_scope": "stored_bytes_and_direct_links",
    }
    validator("record_verification.v1").validate(invalid)
    valid: dict[str, Any] = {**invalid, "valid": True, "findings": []}
    validator("record_verification.v1").validate(valid)
    with pytest.raises(ValidationError):
        validator("record_verification.v1").validate({**invalid, "findings": []})
    with pytest.raises(ValidationError):
        validator("record_verification.v1").validate({**valid, "findings": invalid["findings"]})


@pytest.mark.parametrize(
    "code",
    [
        "FILE_MISSING",
        "FILE_NOT_REGULAR",
        "FILE_NOT_PRIVATE",
        "FILE_READ_FAILED",
        "FILE_SIZE_MISMATCH",
        "FILE_DIGEST_MISMATCH",
    ],
)
def test_file_findings_require_name_and_forbid_record_coordinate(code: str) -> None:
    report = _verification_with_finding(
        {"code": code, "name": "source/note.txt", "record_id": None}
    )
    validator("record_verification.v1").validate(report)
    report["findings"][0] = {"code": code, "name": None, "record_id": "linked"}
    with pytest.raises(ValidationError):
        validator("record_verification.v1").validate(report)


@pytest.mark.parametrize(
    "code",
    [
        "LINK_RECORD_NOT_FOUND",
        "LINK_RECORD_NOT_PUBLISHED",
        "LINK_MANIFEST_INVALID",
        "LINK_DIGEST_MISMATCH",
    ],
)
def test_link_findings_require_record_coordinate_and_forbid_name(code: str) -> None:
    report = _verification_with_finding(
        {"code": code, "name": None, "record_id": "synthetic.input-001"}
    )
    validator("record_verification.v1").validate(report)
    report["findings"][0] = {
        "code": code,
        "name": "source/note.txt",
        "record_id": None,
    }
    with pytest.raises(ValidationError):
        validator("record_verification.v1").validate(report)


def test_one_finding_per_coordinate_and_precedence_remain_runtime_rules() -> None:
    report = _verification_with_finding(
        {"code": "FILE_NOT_PRIVATE", "name": "source/note.txt", "record_id": None}
    )
    report["findings"].append(
        {"code": "FILE_DIGEST_MISMATCH", "name": "source/note.txt", "record_id": None}
    )
    validator("record_verification.v1").validate(report)


def test_record_errors_have_exact_owned_code_reason_action_and_safe_location() -> None:
    document = schema("record_error.v1")
    compiled = validator("record_error.v1")
    for code, branch in document["$defs"].items():
        properties = branch["properties"]
        error = {
            "schema": "record_error.v1",
            "code": code,
            "reason": properties["reason"]["const"],
            "location": "/spec/files/0/path" if code == "SOURCE_READ_FAILED" else "",
            "next_action": properties["next_action"]["const"],
            "retryable": False,
            "exit_code": 2,
        }
        compiled.validate(error)
        with pytest.raises(ValidationError):
            compiled.validate({**error, "reason": "raw private diagnostic"})
    sample = {
        "schema": "record_error.v1",
        "code": "INTERNAL_ERROR",
        "reason": "INTERNAL_ERROR",
        "location": "/private/source/path",
        "next_action": "REPORT_INTERNAL_ERROR",
        "retryable": False,
        "exit_code": 2,
    }
    with pytest.raises(ValidationError):
        compiled.validate(sample)
    with pytest.raises(ValidationError):
        compiled.validate({**sample, "location": "/record\n"})


@pytest.mark.parametrize("name", ["record_error", "record_document"])
def test_generated_record_read_contracts_are_current(name: str) -> None:
    subprocess.run(
        [sys.executable, str(ROOT / f"scripts/build_{name}.py"), "--check"],
        cwd=ROOT,
        check=True,
    )


def test_record_error_vocabulary_matches_its_runtime_owner() -> None:
    from underwrite.cli.records import record_error_payload
    from underwrite.service._record_errors import ERROR_SPECS
    from underwrite.service.records import ERROR_CODES, RecordError

    document = schema("record_error.v1")
    assert set(document["$defs"]) == set(ERROR_SPECS) == set(ERROR_CODES)
    for code in ERROR_SPECS:
        payload = record_error_payload(RecordError(code))
        validator("record_error.v1").validate(payload)
        fields = document["$defs"][code]["properties"]
        assert all(
            payload[key] == fields[key]["const"] for key in ("code", "reason", "next_action")
        )


def test_selected_document_reuses_record_identity_definitions() -> None:
    from underwrite.service.records import RECORD_READ_MAX_BYTES

    document = schema("record_document.v1")
    owner = schema("result_record.v1")
    for name, definition in document["$defs"].items():
        assert definition == owner["$defs"][name]
    assert document["properties"]["bytes"]["maximum"] == RECORD_READ_MAX_BYTES
    assert document["properties"]["verification_scope"] == {
        "const": "selected_file_against_manifest"
    }


@pytest.mark.parametrize("content", [{"state": "error"}, [1, None], "text", 1, 0.5, True, None])
def test_selected_document_accepts_admitted_json_roots_without_full_record_claim(
    content: object,
) -> None:
    document: dict[str, Any] = {
        "schema": "record_document.v1",
        "record_id": "selected",
        "name": "analysis/error.json",
        "media_type": "application/json",
        "bytes": 1,
        "sha256": "sha256:" + "0" * 64,
        "manifest_digest": "sha256:" + "1" * 64,
        "verification_scope": "selected_file_against_manifest",
        "content_representation": "parsed_json",
        "content": content,
    }
    validator("record_document.v1").validate(document)
    for invalid in (
        {**document, "valid": True},
        {**document, "verification_scope": "stored_bytes_and_direct_links"},
    ):
        with pytest.raises(ValidationError):
            validator("record_document.v1").validate(invalid)

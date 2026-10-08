import copy
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, cast

import pytest
from _release_cases import release_case
from _release_cases import release_contracts as release_contracts

from underwrite.instrument.ingest import inspection, release, release_compose
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES
from underwrite.instrument.ingest.release import (
    ReleaseError,
    validate_release_case,
    validate_release_report,
)
from underwrite.instrument.ingest.release import (
    inspect_release as _inspect_release,
)
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.instrument.ingest.transport import TransportError

pytestmark = pytest.mark.usefixtures("release_contracts")

# These JSON fixtures are deliberately malformed by individual contract tests.
JSONFixture = dict[str, Any]


def inspect_release(path: Path) -> JSONFixture:
    return cast(JSONFixture, _inspect_release(path))


class ShapeValidator(Protocol):
    def validate(self, instance: object) -> None: ...


def validate_shape(report: JSONFixture) -> None:
    cast(ShapeValidator, packaged_validator("release_inspection.v1")).validate(report)


def test_real_two_source_flow(tmp_path: Path) -> None:
    report = inspect_release(release_case(tmp_path))
    assert report["result"] == "requirements_matched"
    assert "CONTENT_ADEQUACY_NOT_ASSESSED" in report["limitations"]
    assert report["evidence"][1]["bindings"]["version"]["origin"] == "source_present"


def test_composite_rejects_case_empty_before_reads(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][1]["case"] = {}
    with pytest.raises(ReleaseError):
        validate_release_case(json.dumps(manifest).encode())


def test_relational_report_rejects_false_result(tmp_path: Path) -> None:
    report = inspect_release(release_case(tmp_path))
    changed = copy.deepcopy(report)
    changed["requirements"][0]["evidence_ids"] = []
    with pytest.raises(ReleaseError):
        validate_release_report(changed)


def save(path: Path, manifest: JSONFixture) -> None:
    path.write_text(json.dumps(manifest))


def codes(report: JSONFixture) -> set[str]:
    return {
        item["code"]
        for row in report["requirements"] + report["evidence"]
        for item in row["diagnostics"]
    }


def test_partial_remediation_retains_independent_gaps(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    evaluation = manifest["evidence"].pop(0)
    declared = manifest["evidence"][0]["declared"]
    del declared["evalset"]
    declared["conditions"] = "other"
    corrections: tuple[tuple[Callable[[], None], set[str]], ...] = (
        (lambda: None, {"MISSING_EVIDENCE", "IDENTITY_UNKNOWN", "IDENTITY_MISMATCH"}),
        (
            lambda: manifest["evidence"].append(evaluation),
            {"IDENTITY_UNKNOWN", "IDENTITY_MISMATCH"},
        ),
        (lambda: declared.update(conditions="offline"), {"IDENTITY_UNKNOWN"}),
        (lambda: declared.update(evalset="set"), set()),
    )
    for correction, expected in corrections:
        correction()
        save(path, manifest)
        report = inspect_release(path)
        assert codes(report) == expected
        assert report["limitations"] == list(release_compose.LIMITATIONS)


@pytest.mark.parametrize("field", release.FIELDS)
def test_each_subject_mismatch_survives(tmp_path: Path, field: str) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["subject"][field] = "different"
    save(path, manifest)
    report = inspect_release(path)
    assert report["result"] == "gaps_found"
    assert "IDENTITY_MISMATCH" in codes(report)


@pytest.mark.parametrize(
    "selector,expected",
    [
        (("unknown", "v1"), "UNSUPPORTED_FORMAT"),
        (("underwrite.evidence-bundle", "v1"), "UNSUPPORTED_LINKAGE_PROFILE"),
    ],
)
def test_unsupported_never_opens_and_retains_selector_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selector: tuple[str, str], expected: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][0]["case"]["source"].update(format=selector[0], format_version=selector[1])
    save(path, manifest)
    original = inspection.read_at

    def read(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        assert parts != ["eval.json"]
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(inspection, "read_at", read)
    report = inspect_release(path)
    assert {expected, "SELECTOR_MISMATCH"} <= codes(report)
    assert report["evidence"][0]["artifact"]["raw_hash"] is None
    assert report["requirements"][1]["result"] == "matched"


def test_duplicates_mark_every_member_across_requirements(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][1]["case"] = copy.deepcopy(manifest["evidence"][0]["case"])
    save(path, manifest)
    report = inspect_release(path)
    assert all(
        "DUPLICATE_EVIDENCE" in {item["code"] for item in row["diagnostics"]}
        for row in report["evidence"]
    )


@pytest.mark.parametrize("bad", [{}, {"run_key": "forbidden"}])
def test_nested_input_is_composite_validated(tmp_path: Path, bad: dict[str, str]) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    if bad:
        manifest["evidence"][0]["declared"].update(bad)
    else:
        manifest["evidence"][0]["case"] = bad
    with pytest.raises(ReleaseError):
        validate_release_case(json.dumps(manifest).encode())


@pytest.mark.parametrize("attempted", [True, False])
def test_failed_read_charges_only_attempted_allowance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attempted: bool
) -> None:
    path = release_case(tmp_path)
    original = inspection.read_at
    allowances: list[int] = []

    def read(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        if parts == ["eval.json"]:
            error = TransportError("SOURCE_READ_FAILED")
            error.attempted_read = attempted
            raise error
        if parts == ["trace.json"]:
            allowances.append(limit)
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(release_compose, "BATCH_MAX_BYTES", DEFAULT_MAX_BYTES)
    monkeypatch.setattr(inspection, "read_at", read)
    if attempted:
        with pytest.raises(ReleaseError) as caught:
            inspect_release(path)
        assert caught.value.payload["code"] == "INPUT_LIMIT_EXCEEDED"
    else:
        report = inspect_release(path)
        assert report["evidence"][0]["artifact"]["raw_hash"] is None
        assert report["requirements"][1]["result"] == "matched"
    assert allowances == [0 if attempted else DEFAULT_MAX_BYTES]


def test_manifest_whitespace_only_changes_outer_hash(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    before = inspect_release(path)
    path.write_bytes(path.read_bytes() + b"\n")
    after = inspect_release(path)
    assert before.pop("manifest_hash") != after.pop("manifest_hash")
    assert before == after


REPORT_MUTATIONS: list[Callable[[JSONFixture], None]] = [
    lambda r: r.update(result="gaps_found"),
    lambda r: r["evidence"][0].update(location="/evidence/1"),
    lambda r: r["evidence"][0]["bindings"]["target"].update(declared_value="wrong"),
    lambda r: r["evidence"][0].update(record_presence="unknown"),
    lambda r: r["evidence"][0].update(requirement="missing"),
]


@pytest.mark.parametrize("mutation", REPORT_MUTATIONS)
def test_relational_contradictions(tmp_path: Path, mutation: Callable[[JSONFixture], None]) -> None:
    report = inspect_release(release_case(tmp_path))
    mutation(report)
    with pytest.raises(ReleaseError):
        validate_release_report(report)


@pytest.mark.parametrize(
    "bad_path", ["/absolute", "../escape", "https://example.com/x", "a\\b", "a/../b"]
)
def test_unsafe_paths_fail_before_artifact_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_path: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][1]["case"]["artifact"]["path"] = bad_path
    save(path, manifest)
    original = inspection.read_at

    def read(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        assert parts == [path.name]
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(inspection, "read_at", read)
    with pytest.raises(ReleaseError):
        inspect_release(path)


@pytest.mark.parametrize("kind", ["manifest", "artifact", "ancestor"])
def test_symlinks_fail_closed(tmp_path: Path, kind: str) -> None:
    path = release_case(tmp_path)
    if kind == "manifest":
        link = tmp_path / "link.json"
        link.symlink_to(path)
        with pytest.raises(ReleaseError):
            inspect_release(link)
        return
    target = tmp_path / "eval.json"
    if kind == "artifact":
        moved = tmp_path / "actual.json"
        target.rename(moved)
        target.symlink_to(moved)
    else:
        (tmp_path / "nested").symlink_to(tmp_path, target_is_directory=True)
        manifest = json.loads(path.read_bytes())
        manifest["evidence"][0]["case"]["artifact"]["path"] = "nested/eval.json"
        save(path, manifest)
    report = inspect_release(path)
    assert report["evidence"][0]["artifact"]["raw_hash"] is None
    assert report["requirements"][1]["result"] == "matched"


def test_parent_replacement_keeps_original_descriptor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = release_case(root)
    expected = inspect_release(path)
    original = inspection.read_at

    def read(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        raw = original(parent, parts, limit, flags)
        if parts == ["release.json"]:
            root.rename(tmp_path / "moved")
            root.mkdir()
            (root / "eval.json").write_bytes(b"out-of-root secret")
        return raw

    monkeypatch.setattr(inspection, "read_at", read)
    assert inspect_release(path) == expected


@pytest.mark.parametrize("extra", [0, 1])
def test_manifest_and_aggregate_exact_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, extra: int
) -> None:
    path = release_case(tmp_path)
    raw = path.read_bytes()
    padded = raw + b" " * (inspection.MANIFEST_MAX_BYTES - len(raw) + extra)
    if extra:
        with pytest.raises(ReleaseError):
            validate_release_case(padded)
    else:
        validate_release_case(padded)
    total = sum((tmp_path / name).stat().st_size for name in ("eval.json", "trace.json"))
    monkeypatch.setattr(release_compose, "BATCH_MAX_BYTES", total - extra)
    if extra:
        with pytest.raises(ReleaseError) as caught:
            inspect_release(path)
        assert caught.value.payload["reason"] == "BYTE_LIMIT_EXCEEDED"
    else:
        assert inspect_release(path)["result"] == "requirements_matched"


def test_zero_allowance_eof_has_no_fabricated_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = release_case(tmp_path)
    (tmp_path / "trace.json").write_bytes(b"")
    monkeypatch.setattr(release_compose, "BATCH_MAX_BYTES", (tmp_path / "eval.json").stat().st_size)
    report = inspect_release(path)
    artifact = report["evidence"][1]["artifact"]
    assert artifact["raw_hash"] is not None
    assert artifact["observation"] is None
    assert "INVALID_JSON_INPUT" in codes(report)


def test_original_non_nfc_identity_refused_with_c1_digest_parity(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["subject"]["version"] = "é"
    for entry in manifest["evidence"]:
        entry["declared"]["version"] = "é"
    save(path, manifest)
    artifact_path = tmp_path / "trace.json"
    rows = json.loads(artifact_path.read_bytes())
    for row in rows:
        row["release"] = "e\u0301"
    artifact_path.write_text(json.dumps(rows))
    report = inspect_release(path)
    trace = report["evidence"][1]
    assert trace["bindings"]["version"]["coverage"] == "unusable"
    assert trace["bindings"]["version"]["state"] == "unknown"
    assert "SOURCE_IDENTITY_UNUSABLE" in codes(report)
    case = manifest["evidence"][1]["case"]
    case_path = tmp_path / "case.json"
    case_path.write_text(json.dumps(case))
    c1 = inspection.inspect_artifact(case_path)
    assert c1["observation"] == trace["artifact"]["observation"]


@pytest.mark.parametrize(
    "raw", [b'{"x":1,"x":2}', b'{"x":NaN}', b'"\\ud800"', b"\xff", b"[" * 18 + b"]" * 18]
)
def test_strict_manifest_decode(raw: bytes) -> None:
    with pytest.raises(ReleaseError):
        validate_release_case(raw)


def test_ambiguity_orphan_and_unsupported_missing_are_independent(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    candidate = copy.deepcopy(manifest["evidence"][0])
    candidate["id"] = "second"
    manifest["evidence"].append(candidate)
    manifest["evidence"][1]["requirement"] = "absent"
    manifest["requirements"][1].update(format="unknown")
    save(path, manifest)
    report = inspect_release(path)
    assert {
        "AMBIGUOUS_EVIDENCE",
        "DUPLICATE_EVIDENCE",
        "UNRESOLVED_REQUIREMENT",
        "UNSUPPORTED_FORMAT",
        "MISSING_EVIDENCE",
    } <= codes(report)
    assert report["requirements"][0]["evidence_ids"] == ["eval", "second"]


@pytest.mark.parametrize(
    "body,expected", [(b"[]", "EMPTY_EVIDENCE"), (b"not-json SECRET_PAYLOAD", "INVALID_JSON_INPUT")]
)
def test_empty_and_invalid_payload_keep_sibling(tmp_path: Path, body: bytes, expected: str) -> None:
    path = release_case(tmp_path)
    (tmp_path / "trace.json").write_bytes(body)
    report = inspect_release(path)
    assert expected in codes(report)
    assert report["requirements"][0]["result"] == "matched"
    assert report["evidence"][1]["artifact"]["raw_hash"] is not None
    assert "SECRET_PAYLOAD" not in json.dumps(report)


def test_fifo_does_not_block_or_acquire_identity(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    target = tmp_path / "eval.json"
    target.unlink()
    os.mkfifo(target)
    report = inspect_release(path)
    assert "SOURCE_NOT_REGULAR_FILE" in codes(report)
    assert report["evidence"][0]["artifact"]["raw_hash"] is None


def test_expected_hash_mismatch_skips_payload(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][0]["case"]["artifact"]["expected_hash"] = "sha256:" + "0" * 64
    save(path, manifest)
    report = inspect_release(path)
    assert "EXPECTED_HASH_MISMATCH" in codes(report)
    assert report["evidence"][0]["record_presence"] == "unknown"
    assert report["evidence"][0]["artifact"]["raw_hash"] is not None
    assert report["requirements"][1]["result"] == "matched"


def test_relational_rejects_invented_mismatch_with_equal_values(tmp_path: Path) -> None:
    report = inspect_release(release_case(tmp_path))
    entry = report["evidence"][1]
    entry["bindings"]["version"]["state"] = "mismatched"
    entry["diagnostics"].append(
        dict(
            code="IDENTITY_MISMATCH",
            location=entry["location"],
            field="version",
            source_path="/observations/*/release",
            next_action="CHECK_BINDING_METADATA",
        )
    )
    report["requirements"][1]["result"] = "unresolved"
    report["result"] = "gaps_found"
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize("removed", ["IDENTITY_MISMATCH", "IDENTITY_CONFLICT"])
def test_relational_plural_source_requires_comparison_facts(tmp_path: Path, removed: str) -> None:
    path = release_case(tmp_path)
    trace_path = tmp_path / "trace.json"
    rows = json.loads(trace_path.read_bytes())
    other = copy.deepcopy(rows[0])
    other["release"] = "another-version"
    rows.append(other)
    trace_path.write_text(json.dumps(rows))
    report = inspect_release(path)
    entry = report["evidence"][1]
    entry["diagnostics"] = [
        item
        for item in entry["diagnostics"]
        if not (item["field"] == "version" and item["code"] == removed)
    ]
    if removed == "IDENTITY_CONFLICT":
        entry["bindings"]["version"]["state"] = "ambiguous"
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


def test_unknown_source_stays_unknown_and_plural_without_declaration_has_no_conflict(
    tmp_path: Path,
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    del manifest["evidence"][1]["declared"]["version"]
    save(path, manifest)
    trace_path = tmp_path / "trace.json"
    rows = json.loads(trace_path.read_bytes())
    original = rows[0]["release"]
    for row in rows:
        row["release"] = None
    trace_path.write_text(json.dumps(rows))
    report = inspect_release(path)
    assert report["evidence"][1]["bindings"]["version"]["state"] == "unknown"
    facts = {
        item["code"] for item in report["evidence"][1]["diagnostics"] if item["field"] == "version"
    }
    assert facts == {"IDENTITY_UNKNOWN"}
    rows[0]["release"] = original
    other = copy.deepcopy(rows[0])
    other["release"] = "another-version"
    rows.append(other)
    trace_path.write_text(json.dumps(rows))
    report = inspect_release(path)
    facts = {
        item["code"] for item in report["evidence"][1]["diagnostics"] if item["field"] == "version"
    }
    assert {"IDENTITY_AMBIGUOUS", "IDENTITY_MISMATCH"} <= facts
    assert "IDENTITY_CONFLICT" not in facts


@pytest.mark.parametrize(
    "change",
    [
        "wrong_path",
        "fieldless",
        "wrong_location",
        "nonbinding_field",
        "nonbinding_path",
        "invented_hash",
        "unmapped_path",
    ],
)
def test_diagnostic_attribution_rejects_shape_valid_contradictions(
    tmp_path: Path, change: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][1]["declared"]["version"] = "other"
    save(path, manifest)
    report = inspect_release(path)
    entry = report["evidence"][1]
    fact = entry["diagnostics"][0]
    if change == "wrong_path":
        fact["source_path"] = "/observations/*/environment"
    elif change == "wrong_location":
        fact["location"] += "/case/source"
    elif change == "fieldless":
        entry["diagnostics"].append(
            dict(
                code="IDENTITY_UNKNOWN",
                location=entry["location"],
                field=None,
                source_path=None,
                next_action="PROVIDE_BINDING_METADATA",
            )
        )
    elif change == "unmapped_path":
        entry["diagnostics"].append(
            dict(
                code="IDENTITY_MISMATCH",
                field="target",
                source_path="/observations/*/release",
                location=entry["location"],
                next_action="CHECK_BINDING_METADATA",
            )
        )
        entry["bindings"]["target"].update(state="mismatched", declared_value="other")
    else:
        entry["diagnostics"].append(
            dict(
                code="EXPECTED_HASH_MISMATCH" if change == "invented_hash" else "SELECTOR_MISMATCH",
                location=entry["location"] + "/case/artifact/expected_hash",
                field="version" if change == "nonbinding_field" else None,
                source_path="/observations/*/release" if change == "nonbinding_path" else None,
                next_action="CHECK_EXPECTED_HASH",
            )
        )
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize("change", ["remove", "location", "action"])
def test_hash_mismatch_fact_matches_summary_and_remedy(tmp_path: Path, change: str) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][0]["case"]["artifact"]["expected_hash"] = "sha256:" + "0" * 64
    save(path, manifest)
    report = inspect_release(path)
    entry = report["evidence"][0]
    fact = next(item for item in entry["diagnostics"] if item["code"] == "EXPECTED_HASH_MISMATCH")
    if change == "remove":
        entry["diagnostics"].remove(fact)
    elif change == "location":
        fact["location"] = entry["location"]
    else:
        fact["next_action"] = "CORRECT_ARTIFACT"
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


def test_mapped_source_path_rejects_absent_and_ambiguous_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from underwrite.instrument.ingest import release_bindings

    assert release_bindings.binding_source_path("version", "unavailable") is None
    assert release_bindings.binding_source_path("version", "unmapped") is None
    with pytest.raises(ValueError, match="INVALID_BINDING_SOURCE_PATH"):
        release_bindings.binding_source_path("target", "complete")
    profiles = dict(release_bindings.PROFILES)
    profiles[("test-only", "v1")] = ("rows", {"version": "other"})
    monkeypatch.setattr(release_bindings, "PROFILES", profiles)
    with pytest.raises(ValueError, match="INVALID_BINDING_SOURCE_PATH"):
        release_bindings.binding_source_path("version", "complete")


@pytest.mark.parametrize(
    "code,action,suffix",
    [
        ("MISSING_EVIDENCE", "PROVIDE_REQUIRED_EVIDENCE", ""),
        ("AMBIGUOUS_EVIDENCE", "SELECT_ONE_EVIDENCE", ""),
        ("UNSUPPORTED_FORMAT", "SELECT_SUPPORTED_PROFILE", "/case/source"),
        ("UNSUPPORTED_LINKAGE_PROFILE", "SELECT_SUPPORTED_PROFILE", "/case/source"),
        ("SOURCE_READ_FAILED", "PROVIDE_REGULAR_LOCAL_FILE", "/case/artifact/path"),
        ("INVALID_JSON_INPUT", "CORRECT_ARTIFACT", "/case/artifact"),
        ("MALFORMED_DEEPEVAL_PAYLOAD", "CORRECT_ARTIFACT", "/case/artifact"),
        ("INVALID_OBSERVATION", "CORRECT_ARTIFACT", "/case/artifact"),
    ],
)
def test_diagnostic_container_and_phase_contradictions(
    tmp_path: Path, code: str, action: str, suffix: str
) -> None:
    report = inspect_release(release_case(tmp_path))
    entry = report["evidence"][0]
    entry["diagnostics"].append(
        dict(
            code=code,
            field=None,
            source_path=None,
            location=entry["location"] + suffix,
            next_action=action,
        )
    )
    report["requirements"][0]["result"] = "unresolved"
    report["result"] = "gaps_found"
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize("change", ["remove_read", "hash_plus_decode", "wrong_action"])
def test_phase_failure_is_complete_and_exclusive(tmp_path: Path, change: str) -> None:
    path = release_case(tmp_path)
    if change == "hash_plus_decode":
        manifest = json.loads(path.read_bytes())
        manifest["evidence"][0]["case"]["artifact"]["expected_hash"] = "sha256:" + "0" * 64
        save(path, manifest)
    else:
        (tmp_path / "eval.json").unlink()
    report = inspect_release(path)
    entry = report["evidence"][0]
    if change == "remove_read":
        entry["diagnostics"] = [
            item for item in entry["diagnostics"] if item["code"] != "SOURCE_READ_FAILED"
        ]
    elif change == "wrong_action":
        next(item for item in entry["diagnostics"] if item["code"] == "SOURCE_READ_FAILED")[
            "next_action"
        ] = "CORRECT_MANIFEST"
    else:
        entry["diagnostics"].append(
            dict(
                code="INVALID_JSON_INPUT",
                field=None,
                source_path=None,
                location=entry["location"] + "/case/artifact",
                next_action="CORRECT_ARTIFACT",
            )
        )
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize(
    "code",
    [
        "BYTE_LIMIT_EXCEEDED",
        "SCHEMA_CONFIGURATION_ERROR",
        "USAGE_ERROR",
        "UNSUPPORTED_PLATFORM",
        "SOURCE_NONBLOCKING_UNAVAILABLE",
    ],
)
def test_global_failures_cannot_be_completed_diagnostics(tmp_path: Path, code: str) -> None:
    report = inspect_release(release_case(tmp_path))
    report["evidence"][0]["diagnostics"].append(
        dict(
            code=code,
            location="/evidence/0",
            field=None,
            source_path=None,
            next_action="CORRECT_MANIFEST",
        )
    )
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize("change", ["action", "location"])
def test_requirement_diagnostic_fixed_remedy(tmp_path: Path, change: str) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"].pop(0)
    save(path, manifest)
    report = inspect_release(path)
    diagnostic = report["requirements"][0]["diagnostics"][0]
    diagnostic["next_action" if change == "action" else "location"] = (
        "CORRECT_ARTIFACT" if change == "action" else "/requirements/1"
    )
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


@pytest.mark.parametrize("population", ["missing", "ambiguous"])
def test_requirement_unsupported_classification_is_exclusive(
    tmp_path: Path, population: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["requirements"][0]["format"] = "unknown"
    if population == "missing":
        manifest["evidence"].pop(0)
    else:
        duplicate = copy.deepcopy(manifest["evidence"][0])
        duplicate["id"] = "second"
        manifest["evidence"].append(duplicate)
    save(path, manifest)
    report = inspect_release(path)
    requirement = report["requirements"][0]
    assert {item["code"] for item in requirement["diagnostics"]} == {
        "UNSUPPORTED_FORMAT",
        "MISSING_EVIDENCE" if population == "missing" else "AMBIGUOUS_EVIDENCE",
    }
    validate_release_report(report)
    requirement["diagnostics"].append(
        dict(
            code="UNSUPPORTED_LINKAGE_PROFILE",
            location="/requirements/0",
            field=None,
            source_path=None,
            next_action="SELECT_SUPPORTED_PROFILE",
        )
    )
    validate_shape(report)
    with pytest.raises(ReleaseError, match="INTERNAL_ERROR"):
        validate_release_report(report)


def failure(
    code: str, reason: str, location: str = "", action: str = "CORRECT_MANIFEST"
) -> dict[str, object]:
    return dict(
        schema="release_inspection_error.v1",
        code=code,
        reason=reason,
        location=location,
        next_action=action,
        retryable=False,
        exit_code=2,
    )


@pytest.mark.parametrize(
    "raw,expected",
    [
        (b"{}", failure("INVALID_MANIFEST", "INVALID_MANIFEST")),
        (b"{", failure("INVALID_MANIFEST", "INVALID_JSON_INPUT")),
        (b"[" * 18 + b"]" * 18, failure("INPUT_LIMIT_EXCEEDED", "DEPTH_LIMIT_EXCEEDED")),
        (
            b" " * (inspection.MANIFEST_MAX_BYTES + 1),
            failure("INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", action="REDUCE_BATCH_SIZE"),
        ),
    ],
)
def test_complete_decode_failure_envelopes(raw: bytes, expected: dict[str, object]) -> None:
    with pytest.raises(ReleaseError) as caught:
        validate_release_case(raw)
    assert caught.value.payload == expected


def test_complete_nested_path_and_artifact_limit_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["evidence"][1]["case"]["artifact"]["path"] = "../outside"
    with pytest.raises(ReleaseError) as caught:
        validate_release_case(json.dumps(manifest).encode())
    assert caught.value.payload == failure(
        "INVALID_ARTIFACT_PATH", "INVALID_ARTIFACT_PATH", "/evidence/1/case/artifact/path"
    )
    monkeypatch.setattr(release_compose, "BATCH_MAX_BYTES", (tmp_path / "eval.json").stat().st_size)
    with pytest.raises(ReleaseError) as caught:
        inspect_release(path)
    assert caught.value.payload == failure(
        "INPUT_LIMIT_EXCEEDED",
        "BYTE_LIMIT_EXCEEDED",
        "/evidence/1/case/artifact/path",
        "REDUCE_BATCH_SIZE",
    )


@pytest.mark.parametrize("name", ["release_case", "release_inspection", "observation"])
@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_actual_resource_failures_have_configuration_envelopes(
    tmp_path: Path, name: str, fault: str
) -> None:
    path = release_case(tmp_path)
    resource = tmp_path / "trusted" / "_contracts" / f"{name}.v1.schema.json"
    if fault == "missing":
        resource.unlink()
    else:
        resource.write_bytes(b"{")
    with pytest.raises(ReleaseError) as caught:
        inspect_release(path)
    reason = "INVALID_OBSERVATION_SCHEMA" if name == "observation" else "INVALID_INSPECTION_SCHEMA"
    assert caught.value.payload == failure(
        "SCHEMA_CONFIGURATION_ERROR", reason, action="REINSTALL_PACKAGE"
    )


@pytest.mark.parametrize("shape_valid", [False, True])
def test_complete_internal_failure_envelopes(tmp_path: Path, shape_valid: bool) -> None:
    report = inspect_release(release_case(tmp_path))
    if shape_valid:
        report["requirements"][0]["evidence_ids"] = []
        validate_shape(report)
    else:
        report["extra"] = True
    with pytest.raises(ReleaseError) as caught:
        validate_release_report(report)
    assert caught.value.payload == failure(
        "INTERNAL_ERROR", "INTERNAL_ERROR", action="REPORT_INTERNAL_ERROR"
    )


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("requirements", failure("INVALID_MANIFEST", "INVALID_MANIFEST", "/requirements")),
        ("evidence", failure("INVALID_MANIFEST", "INVALID_MANIFEST", "/evidence")),
        ("subject", failure("INVALID_MANIFEST", "INVALID_MANIFEST", "/subject/target")),
        (
            "declared",
            failure("INVALID_MANIFEST", "INVALID_MANIFEST", "/evidence/0/declared/target"),
        ),
        ("control", failure("INVALID_MANIFEST", "INVALID_MANIFEST")),
        ("declared_control", failure("INVALID_MANIFEST", "INVALID_MANIFEST")),
        ("invalid_id", failure("INVALID_MANIFEST", "INVALID_MANIFEST")),
        ("source", failure("INVALID_SOURCE", "NON_NFC_REFERENCE", "/evidence/0/case/source")),
    ],
)
def test_semantic_input_failure_before_any_artifact_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str, expected: dict[str, object]
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    if kind in {"requirements", "evidence"}:
        manifest[kind][1]["id"] = manifest[kind][0]["id"]
    elif kind == "source":
        manifest["evidence"][0]["case"]["source"]["ref"] = "e\u0301"
    elif kind == "declared":
        manifest["evidence"][0]["declared"]["target"] = "e\u0301"
    elif kind == "declared_control":
        manifest["evidence"][0]["declared"]["target"] = "x\x1by"
    elif kind == "invalid_id":
        manifest["requirements"][0]["id"] = "not/an/id"
    else:
        manifest["subject"]["target"] = "e\u0301" if kind == "subject" else "x\x1by"
    save(path, manifest)
    original = inspection.read_at
    opened: list[list[str]] = []

    def read(parent: int, parts: list[str], limit: int, flags: tuple[int, int]) -> bytes:
        opened.append(parts)
        return original(parent, parts, limit, flags)

    monkeypatch.setattr(inspection, "read_at", read)
    with pytest.raises(ReleaseError) as caught:
        inspect_release(path)
    assert caught.value.payload == expected
    assert opened == [[path.name]]


def test_entirely_absent_declaration_keeps_source_and_unknowns(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    del manifest["evidence"][1]["declared"]
    save(path, manifest)
    report = inspect_release(path)
    bindings = report["evidence"][1]["bindings"]
    assert report["result"] == "gaps_found"
    for field in ("version", "environment"):
        assert bindings[field]["origin"] == "source_present"
        assert bindings[field]["state"] == "matched"
    for field in ("target", "evalset", "evalset_version", "conditions"):
        assert bindings[field] == dict(
            coverage="unmapped",
            origin="unknown",
            state="unknown",
            source_value=None,
            declared_value=None,
        )


@pytest.mark.parametrize("kind", ["nul", "missing", "platform"])
def test_invocation_failures_have_complete_safe_envelopes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    path = tmp_path / ("parent\x00SECRET" if kind == "nul" else "missing") / "release.json"
    expected = failure(
        "SOURCE_READ_FAILED", "SOURCE_READ_FAILED", action="PROVIDE_REGULAR_LOCAL_FILE"
    )
    if kind == "platform":
        monkeypatch.delattr(inspection.os, "O_NOFOLLOW")
        expected = failure(
            "UNSUPPORTED_PLATFORM", "UNSUPPORTED_PLATFORM", action="USE_SUPPORTED_PLATFORM"
        )
    with pytest.raises(ReleaseError) as caught:
        inspect_release(path)
    assert caught.value.payload == expected


@pytest.mark.parametrize("gap", ["duplicate", "selector"])
def test_matched_bindings_do_not_override_independent_evidence_gaps(
    tmp_path: Path, gap: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    if gap == "duplicate":
        manifest["requirements"][1].update(format="deepeval.test-run", format_version="4.1.1")
        manifest["evidence"][1]["case"] = copy.deepcopy(manifest["evidence"][0]["case"])
    else:
        manifest["requirements"][0].update(
            format="langfuse.observations-v2", format_version="4.35.0"
        )
    save(path, manifest)
    report = inspect_release(path)
    affected = report["evidence"] if gap == "duplicate" else report["evidence"][:1]
    for entry in affected:
        assert all(binding["state"] == "matched" for binding in entry["bindings"].values())
        requirement = next(
            row for row in report["requirements"] if row["id"] == entry["requirement"]
        )
        assert requirement["evidence_ids"] == [entry["id"]]
        assert requirement["result"] == "unresolved"
    assert report["result"] == "gaps_found"


@pytest.mark.parametrize(
    "field,value", [("field", "version"), ("source_path", "/observations/*/release")]
)
def test_nonbinding_attribution_changes_only_one_dimension(
    tmp_path: Path, field: str, value: str
) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["requirements"][0].update(format="langfuse.observations-v2", format_version="4.35.0")
    save(path, manifest)
    report = inspect_release(path)
    diagnostic = next(
        item for item in report["evidence"][0]["diagnostics"] if item["code"] == "SELECTOR_MISMATCH"
    )
    diagnostic[field] = value
    validate_shape(report)
    with pytest.raises(ReleaseError) as caught:
        validate_release_report(report)
    assert caught.value.payload == failure(
        "INTERNAL_ERROR", "INTERNAL_ERROR", action="REPORT_INTERNAL_ERROR"
    )


def test_mixed_unusable_source_and_documented_diagnostic_order(tmp_path: Path) -> None:
    path = release_case(tmp_path)
    manifest = json.loads(path.read_bytes())
    manifest["requirements"][1].update(format="deepeval.test-run", format_version="4.1.1")
    del manifest["evidence"][1]["declared"]["target"]
    manifest["evidence"][1]["declared"]["conditions"] = "other"
    save(path, manifest)
    trace_path = tmp_path / "trace.json"
    rows = json.loads(trace_path.read_bytes())
    other = copy.deepcopy(rows[0])
    other["release"] = "e\u0301"
    rows.append(other)
    trace_path.write_text(json.dumps(rows))
    report = inspect_release(path)
    entry = report["evidence"][1]
    assert entry["bindings"]["version"]["coverage"] == "unusable"
    assert entry["bindings"]["version"]["origin"] == "source_present"
    facts = {item["code"] for item in entry["diagnostics"] if item["field"] == "version"}
    assert facts == {"IDENTITY_UNKNOWN", "SOURCE_IDENTITY_UNUSABLE"}
    keys = [
        (item["location"], item["field"] or "", item["code"], item["source_path"] or "")
        for item in entry["diagnostics"]
    ]
    assert keys == sorted(keys)
    assert entry["diagnostics"][0]["code"] == "SELECTOR_MISMATCH"

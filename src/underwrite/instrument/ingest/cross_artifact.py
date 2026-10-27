"""Read selected Goppi and Promptfoo bytes for a descriptive cross-artifact review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.evidence.c55_selected import (
    C55SelectedError,
    validate_selected_c55_projection,
)
from underwrite.instrument.evidence.common_result import (
    ResultInvariantError,
    availability_counts,
    check_result_invariants,
)
from underwrite.instrument.evidence.cross_artifact_relation import (
    RelationError,
    review_declared_cases,
)
from underwrite.instrument.evidence.promptfoo_compat import (
    PromptfooCompatibilityError,
    account_promptfoo_compatible,
)
from underwrite.instrument.ingest.application import DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH
from underwrite.instrument.ingest.schema import packaged_validator
from underwrite.instrument.ingest.transport import (
    DecodeError,
    TransportError,
    decode_json,
    read_local_file,
)

CORRESPONDENCE_SCHEMA = "cross_artifact_correspondence.v1"
RESULT_SCHEMA = "cross_artifact_review.v1"
CORRESPONDENCE_MAX_BYTES = 128 * 1024
CASE_ID_MAX_LENGTH = 256
MAX_PROMPTFOO_ROWS = 1000
GOPPI_ASSESSMENT_SCHEMA = "goppi.operator_answer_exchange_assessment.v1"
GOPPI_READER_PROFILE = "goppi.c55-selected-fields.v1"
PROMPTFOO_LIMITATIONS = [
    "SOURCE_CLAIMS_UNVERIFIED",
    "GOPPI_PORTABLE_CLOSURE_NOT_REPERFORMED",
    "GOPPI_TERMINAL_CONTROLS_NOT_REPERFORMED",
    "INPUT_CORRESPONDENCE_NOT_INDEPENDENTLY_VERIFIED",
    "SEMANTIC_EQUIVALENCE_UNESTABLISHED",
    "PROMPTFOO_PLANNED_POPULATION_UNKNOWN",
    "PROMPTFOO_ROW_ROLES_SOURCE_DECLARED",
    "NO_IMPROVEMENT_OR_APPROVAL",
    "HASHES_AND_COORDINATES_MAY_IDENTIFY_SOURCES",
]


class CrossArtifactInputError(ValueError):
    """Finite refusal without source payloads or local paths."""

    def __init__(self, code: str, reason: str, location: str) -> None:
        self.code, self.reason, self.location = code, reason, location
        super().__init__(reason)


def _read_object(path: Path, *, limit: int, location: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = read_local_file(path, limit)
    except TransportError as exc:
        reason = str(exc)
        code = "INPUT_LIMIT_EXCEEDED" if reason == "BYTE_LIMIT_EXCEEDED" else "SOURCE_READ_FAILED"
        raise CrossArtifactInputError(code, reason, location) from None
    try:
        value = decode_json(raw, DEFAULT_MAX_DEPTH)
    except DecodeError as exc:
        reason = str(exc)
        code = "INPUT_LIMIT_EXCEEDED" if reason == "DEPTH_LIMIT_EXCEEDED" else "INVALID_INPUT"
        raise CrossArtifactInputError(code, reason, location) from None
    if type(value) is not dict:
        raise CrossArtifactInputError("INVALID_INPUT", "OBJECT_REQUIRED", location)
    return cast(dict[str, Any], value), raw


def _validate_schema(value: object, name: str, location: str) -> None:
    try:
        packaged_validator(name).validate(value)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError:
        raise CrossArtifactInputError(
            "INVALID_INPUT", "SCHEMA_VALIDATION_FAILED", location
        ) from None


def _goppi_selected(
    assessment: dict[str, Any], result: dict[str, Any], assessment_hash: str
) -> tuple[list[str], list[dict[str, object]], dict[str, int]]:
    if assessment.get("schema") != GOPPI_ASSESSMENT_SCHEMA:
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE",
            "GOPPI_ASSESSMENT_PROFILE_UNSUPPORTED",
            "/goppi_assessment/schema",
        )
    if result.get("schema") != "evidence_result.v1":
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE", "GOPPI_RESULT_PROFILE_UNSUPPORTED", "/goppi_result/schema"
        )
    _validate_schema(result, "evidence_result.v1", "/goppi_result")
    try:
        check_result_invariants(result)
        case_ids, items = validate_selected_c55_projection(assessment, result, assessment_hash)
    except ResultInvariantError as exc:
        raise CrossArtifactInputError(
            "INVALID_INPUT", exc.reason, "/goppi_result" + exc.location
        ) from None
    except C55SelectedError as exc:
        raise CrossArtifactInputError("INVALID_INPUT", exc.reason, exc.location) from None
    counts = availability_counts(result)
    assert counts is not None
    return case_ids, items, counts


def _promptfoo_rows(payload: dict[str, Any]) -> tuple[list[dict[str, object]], dict[str, object]]:
    try:
        accounting = account_promptfoo_compatible(payload, row_index=True)
    except PromptfooCompatibilityError as exc:
        raise CrossArtifactInputError(exc.code, exc.reason, "/promptfoo" + exc.location) from None
    if accounting["declared_relation"] != "consistent" or accounting["findings"]:
        raise CrossArtifactInputError(
            "INVALID_INPUT", "PROMPTFOO_ACCOUNTING_INCONSISTENT", "/promptfoo/results"
        )
    summary = cast(dict[str, Any], payload["results"])
    source_rows = cast(list[object], summary["results"])
    if len(source_rows) > MAX_PROMPTFOO_ROWS:
        raise CrossArtifactInputError(
            "COMPUTATION_LIMIT_EXCEEDED", "ROW_LIMIT_EXCEEDED", "/promptfoo/results/results"
        )
    indexed_rows = cast(list[dict[str, object]], accounting["rows"])
    return [
        _promptfoo_row(index, source, indexed_rows[index])
        for index, source in enumerate(source_rows)
    ], accounting


def _promptfoo_row(index: int, source: object, indexed: dict[str, object]) -> dict[str, object]:
    location = f"/promptfoo/results/results/{index}"
    if type(source) is not dict:
        raise CrossArtifactInputError("INVALID_INPUT", "PROMPTFOO_ROW_INVALID", location)
    row = cast(dict[str, object], source)
    test_case = row.get("testCase")
    if type(test_case) is not dict:
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE", "PROMPTFOO_CASE_METADATA_REQUIRED", location + "/testCase"
        )
    metadata = cast(dict[str, object], test_case).get("metadata")
    if type(metadata) is not dict:
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE",
            "PROMPTFOO_CASE_METADATA_REQUIRED",
            location + "/testCase/metadata",
        )
    selected = cast(dict[str, object], metadata)
    case_id, slot_kind, success = (
        selected.get("case_id"),
        selected.get("slot_kind"),
        row.get("success"),
    )
    if type(case_id) is not str or not case_id or len(case_id) > CASE_ID_MAX_LENGTH:
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE",
            "PROMPTFOO_CASE_ID_REQUIRED",
            location + "/testCase/metadata/case_id",
        )
    if slot_kind not in ("subject", "control"):
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE",
            "PROMPTFOO_SLOT_KIND_REQUIRED",
            location + "/testCase/metadata/slot_kind",
        )
    if type(success) is not bool:
        raise CrossArtifactInputError(
            "UNSUPPORTED_PROFILE", "PROMPTFOO_BOOLEAN_SUCCESS_REQUIRED", location + "/success"
        )
    assert indexed["row_index"] == index
    category = indexed["category"]
    assert category in ("successes", "failures", "errors")
    if (category == "successes") is not success:
        raise CrossArtifactInputError("INVALID_INPUT", "PROMPTFOO_ROW_CATEGORY_CONFLICT", location)
    return {
        "row_index": index,
        "case_id": case_id,
        "slot_kind": slot_kind,
        "source_success": success,
        "category": category,
    }


def _source_hashes(assessment: bytes, result: bytes, promptfoo: bytes) -> dict[str, str]:
    return {
        "goppi_assessment_sha256": digest_bytes(assessment),
        "goppi_result_sha256": digest_bytes(result),
        "promptfoo_export_sha256": digest_bytes(promptfoo),
    }


def _correspondence(
    declaration: dict[str, Any] | None,
    raw: bytes | None,
    hashes: dict[str, str],
    items: list[dict[str, object]],
    case_ids: list[str],
    rows: list[dict[str, object]],
) -> tuple[str, dict[str, object], dict[str, object], list[str]]:
    if declaration is None:
        subject_indices = [
            cast(int, row["row_index"]) for row in rows if row["slot_kind"] == "subject"
        ]
        relation: dict[str, object] = {
            "declaration_sha256": None,
            "coverage": "none",
            "input_correspondence": "not_declared",
            "semantic_equivalence": "unestablished",
            "condition_alignment": "not_established",
            "pairs": [],
            "unmatched_case_indices": list(range(len(items))),
            "unmatched_subject_row_indices": subject_indices,
        }
        counts: dict[str, object] = {"equal": 0, "different": 0, "ineligible": 0}
        return (
            "incomparable_on_supplied_evidence",
            relation,
            counts,
            ["PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE"],
        )
    _validate_schema(declaration, CORRESPONDENCE_SCHEMA, "/correspondence")
    declared = cast(dict[str, object], declaration["sources"])
    for name, actual in hashes.items():
        if declared[name] != actual:
            raise CrossArtifactInputError(
                "INPUT_BINDING_MISMATCH", "SOURCE_HASH_MISMATCH", f"/correspondence/sources/{name}"
            )
    try:
        reviewed = review_declared_cases(
            items,
            case_ids,
            rows,
            cast(list[dict[str, object]], declaration["pairs"]),
            cast(str, declaration["coverage"]),
        )
    except RelationError as exc:
        raise CrossArtifactInputError(
            "INVALID_INPUT", exc.reason, "/correspondence" + exc.location
        ) from None
    assert raw is not None
    relation = {
        "declaration_sha256": digest_bytes(raw),
        "coverage": declaration["coverage"],
        "input_correspondence": "declared_unverified",
        "semantic_equivalence": "unestablished",
        "condition_alignment": "not_established",
        "pairs": reviewed["pairs"],
        "unmatched_case_indices": reviewed["unmatched_case_indices"],
        "unmatched_subject_row_indices": reviewed["unmatched_subject_row_indices"],
    }
    return (
        "declared_case_mapping_descriptive",
        relation,
        cast(dict[str, object], reviewed["finding_counts"]),
        cast(list[str], reviewed["next_verification_tasks"]),
    )


def _task(
    code: str, owner: str, case_index: int | None = None, row_index: int | None = None
) -> dict[str, object]:
    return {
        "code": code,
        "owner": owner,
        "case_index": case_index,
        "row_index": row_index,
    }


def review_artifact_files(
    goppi_assessment: Path,
    goppi_result: Path,
    promptfoo_export: Path,
    correspondence: Path | None = None,
) -> dict[str, object]:
    """Read each selected file once; compare only source claims under an optional owner map."""
    assessment, assessment_raw = _read_object(
        goppi_assessment, limit=DEFAULT_MAX_BYTES, location="/goppi_assessment"
    )
    result, result_raw = _read_object(
        goppi_result, limit=DEFAULT_MAX_BYTES, location="/goppi_result"
    )
    promptfoo, promptfoo_raw = _read_object(
        promptfoo_export, limit=DEFAULT_MAX_BYTES, location="/promptfoo"
    )
    declaration: dict[str, Any] | None = None
    declaration_raw: bytes | None = None
    if correspondence is not None:
        declaration, declaration_raw = _read_object(
            correspondence, limit=CORRESPONDENCE_MAX_BYTES, location="/correspondence"
        )
    hashes = _source_hashes(assessment_raw, result_raw, promptfoo_raw)
    case_ids, items, availability = _goppi_selected(
        assessment, result, hashes["goppi_assessment_sha256"]
    )
    rows, accounting = _promptfoo_rows(promptfoo)
    relation_state, relation, findings, relation_tasks = _correspondence(
        declaration, declaration_raw, hashes, items, case_ids, rows
    )
    subject_rows = sum(row["slot_kind"] == "subject" for row in rows)
    tasks: list[dict[str, object]] = []
    if relation_state == "incomparable_on_supplied_evidence":
        tasks.append(_task("PROVIDE_SOURCE_BOUND_CASE_CORRESPONDENCE", "evaluation_owner"))
    else:
        tasks.extend(
            (
                _task("VERIFY_INPUT_AND_METRIC_SEMANTICS", "evaluation_owner"),
                _task("PROVIDE_COMPARATOR_CONDITIONS", "evaluation_owner"),
            )
        )
        if relation_tasks:
            tasks.append(_task("DECLARE_REMAINING_CASE_CORRESPONDENCE", "evaluation_owner"))
        for pair in cast(list[dict[str, object]], relation["pairs"]):
            if pair["finding"] == "value_comparison_ineligible":
                tasks.append(
                    _task(
                        "INSPECT_INELIGIBLE_CASE_EVIDENCE",
                        "calling_agent",
                        cast(int, pair["case_index"]),
                        cast(int, pair["row_index"]),
                    )
                )
    tasks.append(_task("VERIFY_GOPPI_PORTABLE_CLOSURE", "source_reviewer"))
    tasks.append(_task("VERIFY_SOURCE_PROVENANCE", "source_reviewer"))
    population = cast(dict[str, object], result["population"])
    category_counts = cast(dict[str, int], accounting["row_tallies"])
    promptfoo_source = cast(dict[str, object], accounting["source"])
    report: dict[str, object] = {
        "schema": RESULT_SCHEMA,
        "relation": relation_state,
        "source_identity": hashes,
        "readers": {
            "goppi": {
                "profile": GOPPI_READER_PROFILE,
                "assessment_schema": GOPPI_ASSESSMENT_SCHEMA,
                "result_schema": "evidence_result.v1",
            },
            "promptfoo": {
                "profile": accounting["reader_profile"],
                "producer_version": promptfoo_source["producer_version"],
                "producer_version_state": promptfoo_source["producer_version_state"],
            },
        },
        "populations": {
            "goppi": {
                "unit": "case",
                "planned_count": population["planned_count"],
                "item_count": population["observed_count"],
                "availability": {
                    name: availability[name]
                    for name in ("observed", "missing", "error", "abstained")
                },
            },
            "promptfoo": {
                "unit": "row",
                "supplied_rows": len(rows),
                "subject_rows": subject_rows,
                "control_rows": len(rows) - subject_rows,
                "planned_count": None,
                "category_counts": {
                    name: category_counts[name] for name in ("successes", "failures", "errors")
                },
            },
        },
        "correspondence": relation,
        "finding_counts": findings,
        "improvement": "unknown",
        "checks": {
            "declaration_source_hashes": "matched" if declaration is not None else "not_supplied",
            "goppi_assessment_result_binding": "checked_selected_fields",
            "goppi_terminal_controls": "not_reperformed",
            "goppi_portable_closure": "not_reperformed",
            "source_authentication": "not_independently_verified",
        },
        "next_verification_tasks": tasks,
        "limitations": PROMPTFOO_LIMITATIONS.copy(),
    }
    # The underlying Promptfoo accounting is required to admit the supplied rows, not to
    # assert its producer stats are a planned case inventory.
    assert cast(dict[str, int], accounting["row_tallies"])["total"] == len(rows)
    _validate_schema(report, RESULT_SCHEMA, "")
    return report

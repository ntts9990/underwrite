"""Audit bounded binary producer runs without comparison or inference."""

from __future__ import annotations

import unicodedata
from collections import Counter
from dataclasses import dataclass
from typing import Literal, cast

from underwrite_core.canonical import CanonicalizationError, canonical_bytes, digest_bytes

INPUT_MAX_BYTES = 4 * 1024 * 1024
OUTPUT_MAX_BYTES = 8 * 1024 * 1024
MAX_GROUPS = 32
MAX_RECORDS = 4096
MAX_TOTAL_WORK = 8192
MAX_PLANNED_UNITS = 4096
MAX_CONTEXT_ENTRIES = 16
MAX_SOURCE_FILES = 64
MAX_SOURCE_REFS = 16
MAX_NON_BINARY_CLASSES = 16
MAX_REPEAT_INDEX = 1024
SAFE_INTEGER = 2**53 - 1
SHA256_DIGEST_LENGTH = 71

MissingReason = Literal["infra", "verifier", "not_started", "unknown"]
MISSING_REASONS: tuple[MissingReason, ...] = (
    "infra",
    "verifier",
    "not_started",
    "unknown",
)
BASE_LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "GROUPS_NOT_COMPARABLE",
    "PRODUCER_EXECUTION_UNITS_NOT_ESTABLISHED_AS_INDEPENDENT_SAMPLES",
    "NO_STATISTICAL_INFERENCE",
    "NO_DECISION_AUTHORITY",
)


class RunAuditError(ValueError):
    """A bounded diagnostic code; no producer-controlled text is exposed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _invalid(code: str = "INVALID_INPUT") -> RunAuditError:
    return RunAuditError(code)


def _object(value: object, required: set[str], allowed: set[str]) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid()
    result = cast(dict[object, object], value)
    if (
        any(type(key) is not str for key in result)
        or not required <= result.keys()
        or not result.keys() <= allowed
    ):
        raise _invalid()
    return cast(dict[str, object], result)


def _text(value: object, *, maximum: int = 128, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise _invalid()
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise _invalid() from exc
    if not unicodedata.is_normalized("NFC", value):
        raise _invalid()
    return value


def _required_text(value: object, *, maximum: int = 128) -> str:
    return cast(str, _text(value, maximum=maximum))


def _integer(value: object, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _invalid()
    return value


def _list(value: object, maximum: int) -> list[object]:
    if type(value) is not list:
        raise _invalid()
    result = cast(list[object], value)
    if len(result) > maximum:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    return result


def _digest(value: object, *, nullable: bool = False) -> str | None:
    text = _text(value, maximum=SHA256_DIGEST_LENGTH, nullable=nullable)
    if text is not None and (
        len(text) != SHA256_DIGEST_LENGTH
        or not text.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in text[7:])
    ):
        raise _invalid()
    return text


def _source(value: object) -> dict[str, object]:
    keys = {"producer", "format", "format_version", "artifact_digest", "files"}
    source = _object(value, keys, keys)
    files: list[dict[str, object]] = []
    names: set[str] = set()
    for item in _list(source["files"], MAX_SOURCE_FILES):
        entry = _object(item, {"name", "sha256", "bytes"}, {"name", "sha256", "bytes"})
        name = _required_text(entry["name"], maximum=512)
        if name in names:
            raise _invalid("DUPLICATE_SOURCE_FILE")
        names.add(name)
        files.append(
            {
                "name": name,
                "sha256": _digest(entry["sha256"]),
                "bytes": _integer(entry["bytes"], 0, SAFE_INTEGER),
            }
        )
    return {
        "producer": _required_text(source["producer"]),
        "format": _required_text(source["format"]),
        "format_version": _required_text(source["format_version"]),
        "artifact_digest": _digest(source["artifact_digest"]),
        "files": sorted(files, key=lambda item: cast(str, item["name"])),
    }


def _outcome_definition(value: object) -> dict[str, str]:
    keys = {"name", "positive_means"}
    definition = _object(value, keys, keys)
    return {
        "name": _required_text(definition["name"]),
        "positive_means": _required_text(definition["positive_means"], maximum=512),
    }


def _context(value: object) -> dict[str, object]:
    if type(value) is not dict:
        raise _invalid()
    source = cast(dict[object, object], value)
    if len(source) > MAX_CONTEXT_ENTRIES:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    result: dict[str, object] = {}
    for key, item in source.items():
        name = _required_text(key, maximum=128)
        if type(item) is str:
            result[name] = _required_text(item, maximum=512)
        elif type(item) is bool or item is None:
            result[name] = item
        else:
            raise _invalid()
    return result


def _source_refs(value: object) -> list[dict[str, object]]:
    refs: list[dict[str, object]] = []
    for item in _list(value, MAX_SOURCE_REFS):
        ref = _object(item, {"kind", "ref", "digest"}, {"kind", "ref", "digest"})
        refs.append(
            {
                "kind": _required_text(ref["kind"]),
                "ref": _required_text(ref["ref"], maximum=512),
                "digest": _digest(ref["digest"], nullable=True),
            }
        )
    return sorted(
        refs,
        key=lambda item: (
            cast(str, item["kind"]),
            cast(str, item["ref"]),
            cast(str | None, item["digest"]) or "",
        ),
    )


@dataclass(frozen=True)
class _Record:
    unit_id: str
    case_id: str
    arm: str
    repeat_index: int
    outcome: int | None
    missing_reason: MissingReason | None
    non_binary_classification: str | None
    source_reason: str | None
    source_refs: list[dict[str, object]]

    def render(self) -> dict[str, object]:
        result: dict[str, object] = {
            "unit_id": self.unit_id,
            "case_id": self.case_id,
            "arm": self.arm,
            "repeat_index": self.repeat_index,
            "source_reason": self.source_reason,
            "source_refs": self.source_refs,
        }
        if self.outcome is not None:
            result["outcome"] = self.outcome
        elif self.missing_reason is not None:
            result["missing_reason"] = self.missing_reason
        else:
            result["non_binary_classification"] = self.non_binary_classification
        return result


def _record(value: object) -> _Record:
    common = {
        "unit_id",
        "case_id",
        "arm",
        "repeat_index",
        "source_reason",
        "source_refs",
    }
    states = {"outcome", "missing_reason", "non_binary_classification"}
    data = _object(value, common, common | states)
    present = states & data.keys()
    if len(present) != 1:
        raise _invalid()
    outcome: int | None = None
    missing: MissingReason | None = None
    classification: str | None = None
    if "outcome" in data:
        outcome = _integer(data["outcome"], 0, 1)
    elif "missing_reason" in data:
        reason = data["missing_reason"]
        if type(reason) is not str or reason not in MISSING_REASONS:
            raise _invalid()
        missing = reason
    else:
        classification = _required_text(data["non_binary_classification"])
    return _Record(
        unit_id=_required_text(data["unit_id"]),
        case_id=_required_text(data["case_id"]),
        arm=_required_text(data["arm"]),
        repeat_index=_integer(data["repeat_index"], 0, MAX_REPEAT_INDEX),
        outcome=outcome,
        missing_reason=missing,
        non_binary_classification=classification,
        source_reason=_text(data["source_reason"], maximum=256, nullable=True),
        source_refs=_source_refs(data["source_refs"]),
    )


@dataclass(frozen=True)
class _Group:
    group_id: str
    context: dict[str, object]
    planned_units: dict[str, str] | None
    records: list[_Record]


def _group(value: object) -> _Group:
    keys = {"group_id", "context", "planned_units", "records"}
    data = _object(value, keys, keys)
    planned_value = data["planned_units"]
    planned: dict[str, str] | None = None
    if planned_value is not None:
        planned = {}
        for item in _list(planned_value, MAX_PLANNED_UNITS):
            slot = _object(item, {"unit_id", "arm"}, {"unit_id", "arm"})
            unit_id = _required_text(slot["unit_id"])
            if unit_id in planned:
                raise _invalid("DUPLICATE_UNIT_ID")
            planned[unit_id] = _required_text(slot["arm"])
    records = [_record(item) for item in _list(data["records"], MAX_RECORDS)]
    by_unit: dict[str, _Record] = {}
    for record in records:
        if record.unit_id in by_unit:
            raise _invalid("DUPLICATE_UNIT_ID")
        by_unit[record.unit_id] = record
        if planned is not None:
            if record.unit_id not in planned:
                raise _invalid("UNPLANNED_UNIT")
            if planned[record.unit_id] != record.arm:
                raise _invalid("PLANNED_ARM_MISMATCH")
    classifications = {record.non_binary_classification for record in records}
    classifications.discard(None)
    if len(classifications) > MAX_NON_BINARY_CLASSES:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    return _Group(
        _required_text(data["group_id"]),
        _context(data["context"]),
        planned,
        sorted(records, key=lambda record: record.unit_id),
    )


def _bucket(
    records: list[_Record], planned: dict[str, str] | None, arm: str | None = None
) -> dict[str, object]:
    selected = [record for record in records if arm is None or record.arm == arm]
    planned_ids = (
        None
        if planned is None
        else {
            unit_id for unit_id, planned_arm in planned.items() if arm is None or planned_arm == arm
        }
    )
    classifications = Counter(
        record.non_binary_classification
        for record in selected
        if record.non_binary_classification is not None
    )
    reported_ids = {record.unit_id for record in selected}
    return {
        "planned": None if planned_ids is None else len(planned_ids),
        "reported": len(selected),
        "observed_one": sum(record.outcome == 1 for record in selected),
        "observed_zero": sum(record.outcome == 0 for record in selected),
        "missing_by_reason": {
            reason: sum(record.missing_reason == reason for record in selected)
            for reason in MISSING_REASONS
        },
        "non_binary_by_classification": [
            {"classification": classification, "count": classifications[classification]}
            for classification in sorted(classifications)
        ],
        "unreported_planned": (None if planned_ids is None else len(planned_ids - reported_ids)),
        "unique_cases": len({record.case_id for record in selected}),
    }


def _normalized_inventory(value: object) -> tuple[dict[str, object], list[_Group]]:
    keys = {"schema", "source", "outcome_definition", "unit", "groups"}
    data = _object(value, keys, keys)
    if data["schema"] != "binary_run_inventory.v1":
        raise _invalid("UNSUPPORTED_PROFILE")
    if data["unit"] != "producer_execution_unit":
        raise _invalid("UNSUPPORTED_PROFILE")
    groups = [_group(item) for item in _list(data["groups"], MAX_GROUPS)]
    group_ids = [group.group_id for group in groups]
    if len(group_ids) != len(set(group_ids)):
        raise _invalid("DUPLICATE_GROUP_ID")
    record_count = sum(len(group.records) for group in groups)
    planned_count = sum(len(group.planned_units or {}) for group in groups)
    if record_count > MAX_RECORDS or record_count + planned_count > MAX_TOTAL_WORK:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    groups.sort(key=lambda group: group.group_id)
    normalized_groups: list[dict[str, object]] = []
    for group in groups:
        normalized_groups.append(
            {
                "group_id": group.group_id,
                "context": group.context,
                "planned_units": (
                    None
                    if group.planned_units is None
                    else [
                        {"unit_id": unit_id, "arm": group.planned_units[unit_id]}
                        for unit_id in sorted(group.planned_units)
                    ]
                ),
                "records": [record.render() for record in group.records],
            }
        )
    normalized: dict[str, object] = {
        "schema": "binary_run_inventory.v1",
        "source": _source(data["source"]),
        "outcome_definition": _outcome_definition(data["outcome_definition"]),
        "unit": "producer_execution_unit",
        "groups": normalized_groups,
    }
    try:
        encoded = canonical_bytes(normalized)
    except (CanonicalizationError, RecursionError) as exc:
        raise _invalid() from exc
    if len(encoded) > INPUT_MAX_BYTES:
        raise _invalid("INPUT_LIMIT_EXCEEDED")
    return normalized, groups


def audit_run_inventory(value: object) -> dict[str, object]:
    """Return descriptive accounting for explicit producer execution units."""
    normalized, groups = _normalized_inventory(value)
    encoded = canonical_bytes(normalized)
    rendered_groups: list[dict[str, object]] = []
    any_non_binary = False
    unit_count = 0
    for group in groups:
        arms = {record.arm for record in group.records}
        if group.planned_units is not None:
            arms.update(group.planned_units.values())
        any_non_binary |= any(
            record.non_binary_classification is not None for record in group.records
        )
        unit_count += len(
            {record.unit_id for record in group.records} | set(group.planned_units or {})
        )
        rendered_groups.append(
            {
                "group_id": group.group_id,
                "context": group.context,
                "records": [record.render() for record in group.records],
                "accounting": _bucket(group.records, group.planned_units),
                "arms": [
                    {"arm": arm, **_bucket(group.records, group.planned_units, arm)}
                    for arm in sorted(arms)
                ],
            }
        )
    limitations: list[str] = list(BASE_LIMITATIONS)
    if any_non_binary:
        limitations.append("NON_BINARY_CLASSIFICATIONS_EXCLUDED_FROM_BINARY_OUTCOMES")
    return {
        "schema": "binary_run_audit.v1",
        "input_digest": digest_bytes(encoded),
        "source": normalized["source"],
        "outcome_definition": normalized["outcome_definition"],
        "unit": "producer_execution_unit",
        "group_count": len(groups),
        "unit_count": unit_count,
        "groups": rendered_groups,
        "limitations": limitations,
    }

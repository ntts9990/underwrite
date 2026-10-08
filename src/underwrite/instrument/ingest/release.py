"""Bounded local declared release linkage, without evaluation or authorization.

The entry point and the independent re-check of a composed report: every claim the
report makes about its own evidence is recomputed here from the report alone, without
reopening a source. ``release_compose`` owns report construction; this module imports that half
and never the other way round."""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

from underwrite.instrument.ingest import inspection
from underwrite.instrument.ingest.application import DIAGNOSTICS, NEXT_ACTIONS
from underwrite.instrument.ingest.release_bindings import (
    FIELDS,
    binding_source_path,
    binding_state,
    usable_identity,
)
from underwrite.instrument.ingest.release_compose import (
    ReleaseError,
    Row,
    as_row,
    as_rows,
    compose,
    is_eligible,
    release_error,
    validate_release_case,
    validate_schema,
)
from underwrite.instrument.ingest.transport import TransportError

REQUIREMENT_ACTIONS = {
    "MISSING_EVIDENCE": "PROVIDE_REQUIRED_EVIDENCE",
    "AMBIGUOUS_EVIDENCE": "SELECT_ONE_EVIDENCE",
}


ENTRY_ACTIONS = {
    "UNRESOLVED_REQUIREMENT": "CORRECT_MANIFEST",
    "SELECTOR_MISMATCH": "SELECT_REQUIRED_PROFILE",
    "DUPLICATE_EVIDENCE": "REMOVE_DUPLICATE_REFERENCE",
    "EMPTY_EVIDENCE": "PROVIDE_NONEMPTY_EVIDENCE",
}


UNSUPPORTED = {"UNSUPPORTED_FORMAT", "UNSUPPORTED_LINKAGE_PROFILE"}


READ_REASONS = set(cast(dict[str, str], DIAGNOSTICS[TransportError][2]).values()) - {
    "BYTE_LIMIT_EXCEEDED",
    "SOURCE_NONBLOCKING_UNAVAILABLE",
}


BUFFER_REASONS = {
    reason
    for error, (category, exit_code, reasons) in DIAGNOSTICS.items()
    if error is not TransportError
    and exit_code == 1
    and category in {"INVALID_INPUT", "INVALID_OBSERVATION"}
    for reason in ([reasons] if isinstance(reasons, str) else reasons.values())
} | {"INT_OUT_OF_SAFE_RANGE"}


def _check_binding(binding: Row, facts: set[object], unavailable: bool, subject: object) -> None:
    source, declared = binding["source_value"], binding["declared_value"]
    plural = binding["origin"] == "source_present" and source is None
    mismatch = (
        plural
        or (source is not None and source != subject)
        or (declared is not None and declared != subject)
    )
    conflict = declared is not None and (plural or (source is not None and source != declared))
    unknown = (
        binding["coverage"] in {"unavailable", "partial", "unusable"}
        or binding["origin"] == "unknown"
    )
    invalid = (
        binding["state"] != binding_state(facts),
        (binding["coverage"] == "unavailable") != unavailable,
        unknown != ("IDENTITY_UNKNOWN" in facts),
        (binding["coverage"] == "unusable") != ("SOURCE_IDENTITY_UNUSABLE" in facts),
        source is not None and not usable_identity(source),
        declared is not None and not usable_identity(declared),
        mismatch != ("IDENTITY_MISMATCH" in facts),
        conflict != ("IDENTITY_CONFLICT" in facts),
        plural != ("IDENTITY_AMBIGUOUS" in facts),
    )
    if any(invalid):
        raise ValueError


def _identity_action(item: Row, entry: Row | None) -> str:
    field = item["field"]
    if entry is None or field not in FIELDS:
        raise ValueError
    coverage = as_row(as_row(entry["bindings"])[str(field)])["coverage"]
    if item["source_path"] != binding_source_path(str(field), str(coverage)):
        raise ValueError
    return (
        "PROVIDE_BINDING_METADATA"
        if item["code"] == "IDENTITY_UNKNOWN"
        else "CHECK_BINDING_METADATA"
    )


def _check_diagnostic(item: Row, location: str, entry: Row | None = None) -> str | None:
    code = str(item["code"])
    path, phase = location, None
    if code.startswith("IDENTITY_") or code == "SOURCE_IDENTITY_UNUSABLE":
        action = _identity_action(item, entry)
    elif item["field"] is not None or item["source_path"] is not None:
        raise ValueError
    elif code in UNSUPPORTED:
        action = "SELECT_SUPPORTED_PROFILE"
        path, phase = (location, None) if entry is None else (location + "/case/source", "unread")
    elif entry is None:
        action = REQUIREMENT_ACTIONS[code]
    elif code in ENTRY_ACTIONS:
        action = ENTRY_ACTIONS[code]
    elif code in READ_REASONS:
        path, phase, action = (
            location + "/case/artifact/path",
            "unread",
            NEXT_ACTIONS["SOURCE_READ_FAILED"],
        )
    elif code in BUFFER_REASONS:
        path, phase, action = location + "/case/artifact", "buffer", "CORRECT_ARTIFACT"
    elif code == "EXPECTED_HASH_MISMATCH":
        path, phase, action = (
            location + "/case/artifact/expected_hash",
            "hash",
            "CHECK_EXPECTED_HASH",
        )
    else:
        raise ValueError
    if item["location"] != path or item["next_action"] != action:
        raise ValueError
    return phase


def _check_evidence(
    entry: Row, index: int, evidence: list[Row], ids: set[object], subject: Row
) -> None:
    location = f"/evidence/{index}"
    diagnostics = as_rows(entry["diagnostics"])
    codes = {item["code"] for item in diagnostics}
    artifact = as_row(entry["artifact"])
    duplicate = (
        artifact["raw_hash"] is not None
        and sum(as_row(other["artifact"])["raw_hash"] == artifact["raw_hash"] for other in evidence)
        > 1
    )
    invalid = (
        entry["location"] != location,
        (entry["requirement"] not in ids) != ("UNRESOLVED_REQUIREMENT" in codes),
        duplicate != ("DUPLICATE_EVIDENCE" in codes),
        (artifact["projection"] == "created") != (entry["record_presence"] != "unknown"),
        (entry["record_presence"] == "empty") != ("EMPTY_EVIDENCE" in codes),
        (artifact["expected_hash_check"] == "mismatched") != ("EXPECTED_HASH_MISMATCH" in codes),
    )
    if any(invalid):
        raise ValueError
    phases = [phase for item in diagnostics if (phase := _check_diagnostic(item, location, entry))]
    expected = (
        []
        if artifact["projection"] == "created"
        else [
            "unread"
            if artifact["raw_hash"] is None
            else "hash"
            if artifact["expected_hash_check"] == "mismatched"
            else "buffer"
        ]
    )
    if phases != expected:
        raise ValueError
    for field in FIELDS:
        facts = {item["code"] for item in diagnostics if item["field"] == field}
        _check_binding(
            as_row(as_row(entry["bindings"])[field]),
            facts,
            artifact["projection"] == "not_created",
            subject[field],
        )


def _check_requirement(requirement: Row, index: int, evidence: list[Row]) -> None:
    candidates = [entry for entry in evidence if entry["requirement"] == requirement["id"]]
    diagnostics = as_rows(requirement["diagnostics"])
    for item in diagnostics:
        _check_diagnostic(item, f"/requirements/{index}")
    codes = {item["code"] for item in diagnostics}
    matched = not diagnostics and len(candidates) == 1 and is_eligible(candidates[0])
    invalid = (
        requirement["evidence_ids"] != [entry["id"] for entry in candidates],
        len(codes & UNSUPPORTED) > 1,
        (not candidates) != ("MISSING_EVIDENCE" in codes),
        (len(candidates) > 1) != ("AMBIGUOUS_EVIDENCE" in codes),
        (requirement["result"] == "matched") != matched,
    )
    if any(invalid):
        raise ValueError


def validate_release_report(report: Row) -> None:
    """Validate shape and cross-entry consistency without reopening source data."""
    try:
        validate_schema(report, "release_inspection.v1")
        requirements, evidence = as_rows(report["requirements"]), as_rows(report["evidence"])
        for entries in (requirements, evidence):
            if len({entry["id"] for entry in entries}) != len(entries):
                raise ValueError
        ids = {entry["id"] for entry in requirements}
        for index, entry in enumerate(evidence):
            _check_evidence(entry, index, evidence, ids, as_row(report["subject"]))
        for index, requirement in enumerate(requirements):
            _check_requirement(requirement, index, evidence)
        matched = all(entry["result"] == "matched" for entry in requirements) and all(
            entry["requirement"] in ids for entry in evidence
        )
        if (report["result"] == "requirements_matched") != matched or not all(
            usable_identity(value) for value in as_row(report["subject"]).values()
        ):
            raise ValueError
    except Exception as exc:
        if isinstance(exc, ReleaseError) and exc.payload["code"] == "SCHEMA_CONFIGURATION_ERROR":
            raise
        raise ReleaseError(
            "INTERNAL_ERROR", "INTERNAL_ERROR", "", NEXT_ACTIONS["INTERNAL_ERROR"]
        ) from exc


def inspect_release(path: Path) -> Row:
    """Read the caller-selected parent once; descendants never reacquire it."""
    try:
        flags = inspection.constrained_flags()
        if "\x00" in str(path):
            raise TransportError("SOURCE_READ_FAILED")
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            raw = inspection.read_at(parent, [path.name], inspection.MANIFEST_MAX_BYTES, flags)
            report = compose(parent, raw, validate_release_case(raw), flags)
            validate_release_report(report)
            return report
        finally:
            os.close(parent)
    except inspection.InspectionError as exc:
        raise release_error(exc) from exc
    except tuple(DIAGNOSTICS) as exc:
        raise release_error(exc) from exc
    except OSError as exc:
        raise ReleaseError(
            "SOURCE_READ_FAILED", "SOURCE_READ_FAILED", "", NEXT_ACTIONS["SOURCE_READ_FAILED"]
        ) from exc

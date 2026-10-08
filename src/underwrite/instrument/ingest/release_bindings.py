"""Finite release identity facts from the original, pre-normalization Projection."""

import unicodedata
from collections.abc import Container, Mapping
from types import MappingProxyType
from typing import TypeGuard, cast

from underwrite.instrument.ingest.project import Projection

FIELDS = ("target", "version", "environment", "evalset", "evalset_version", "conditions")
MAX_IDENTITY_LENGTH = 128
PROFILES: Mapping[tuple[str, str], tuple[str, Mapping[str, str]]] = MappingProxyType(
    {
        ("deepeval.test-run", "4.1.1"): ("testCases", MappingProxyType({})),
        ("langfuse.observations-v2", "4.35.0"): (
            "observations",
            MappingProxyType({"version": "release", "environment": "environment"}),
        ),
    }
)


def usable_identity(value: object) -> TypeGuard[str]:
    """Accept exact bounded NFC scalar identities, without trimming or coercion."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= MAX_IDENTITY_LENGTH
        and bool(value.strip())
        and unicodedata.is_normalized("NFC", value)
        and all(unicodedata.category(char) not in {"Cc", "Cs"} for char in value)
    )


def binding_source_path(field: str, coverage: str) -> str | None:
    """Locate a mapped identity in the fixed profiles, never choose an ambiguity."""
    if coverage in {"unavailable", "unmapped"}:
        return None
    paths = {
        f"/{records}/*/{mapping[field]}"
        for records, mapping in PROFILES.values()
        if field in mapping
    }
    if len(paths) != 1:
        raise ValueError("INVALID_BINDING_SOURCE_PATH")
    return paths.pop()


def binding_state(codes: Container[str]) -> str:
    """Summarize binding facts without discarding lower-priority diagnostics."""
    return next(
        (
            state
            for code, state in (
                ("IDENTITY_CONFLICT", "conflict"),
                ("IDENTITY_AMBIGUOUS", "ambiguous"),
                ("IDENTITY_MISMATCH", "mismatched"),
                ("IDENTITY_UNKNOWN", "unknown"),
            )
            if code in codes
        ),
        "matched",
    )


def _binding(
    values: list[object] | None,
    mapped: bool,
    requested: str,
    declared: str | None,
) -> tuple[dict[str, str | None], set[str]]:
    usable = {value for value in values or [] if usable_identity(value)}
    missing = any(value is None or value == "" for value in values or [])
    unusable = any(
        value is not None and value != "" and not usable_identity(value) for value in values or []
    )
    codes: set[str] = set()
    if values is None:
        coverage = "unavailable"
    elif not mapped:
        coverage = "unmapped"
    elif unusable:
        coverage = "unusable"
        codes.add("SOURCE_IDENTITY_UNUSABLE")
    elif not usable:
        coverage = "absent"
    else:
        coverage = "partial" if missing else "complete"
    fallback = coverage in {"unmapped", "absent"} and declared is not None
    origin = "source_present" if usable else "declared" if fallback else "unknown"
    if coverage in {"unavailable", "partial", "unusable"} or (not usable and not fallback):
        codes.add("IDENTITY_UNKNOWN")
    if len(usable) > 1:
        codes.add("IDENTITY_AMBIGUOUS")
    if any(value != requested for value in usable) or (
        declared is not None and declared != requested
    ):
        codes.add("IDENTITY_MISMATCH")
    if declared is not None and any(value != declared for value in usable):
        codes.add("IDENTITY_CONFLICT")
    return {
        "coverage": coverage,
        "origin": origin,
        "state": binding_state(codes),
        "source_value": next(iter(usable)) if len(usable) == 1 else None,
        "declared_value": declared,
    }, codes


def binding_facts(
    projection: Projection | None,
    selector: tuple[str, str],
    subject: Mapping[str, str],
    declared: Mapping[str, str],
    location: str,
) -> tuple[dict[str, dict[str, str | None]], list[dict[str, str | None]], str]:
    """Compare already-validated declarations and codec-owned original records.

    The caller supplies a supported profile whenever projection is available;
    unknown selectors are supported only with an unavailable projection.
    """
    rows: list[dict[str, object]] | None = None
    mapped: Mapping[str, str] = {}
    if projection is not None:
        record_key, mapped = PROFILES[selector]
        rows = cast(list[dict[str, object]], projection.payload[record_key])
    presence = "unknown" if rows is None else "present" if rows else "empty"
    bindings: dict[str, dict[str, str | None]] = {}
    diagnostics: list[dict[str, str | None]] = []
    for field in FIELDS:
        source_key = mapped.get(field)
        values = (
            None if rows is None else [row.get(source_key) for row in rows] if source_key else []
        )
        bindings[field], codes = _binding(
            values, source_key is not None, subject[field], declared.get(field)
        )
        for code in codes:
            diagnostics.append(
                {
                    "code": code,
                    "location": location,
                    "field": field,
                    "source_path": binding_source_path(field, str(bindings[field]["coverage"])),
                    "next_action": "PROVIDE_BINDING_METADATA"
                    if code == "IDENTITY_UNKNOWN"
                    else "CHECK_BINDING_METADATA",
                }
            )
    if presence == "empty":
        diagnostics.append(
            {
                "code": "EMPTY_EVIDENCE",
                "location": location,
                "field": None,
                "source_path": None,
                "next_action": "PROVIDE_NONEMPTY_EVIDENCE",
            }
        )
    diagnostics.sort(
        key=lambda item: (
            item["location"] or "",
            item["field"] or "",
            item["code"] or "",
            item["source_path"] or "",
        )
    )
    return bindings, diagnostics, presence

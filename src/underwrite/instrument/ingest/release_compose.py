"""Compose inspection records from digest-bound release declarations.

Composition inspects references and bindings without reopening external sources
or turning source claims into independently verified evidence."""

from __future__ import annotations

from typing import cast

from jsonschema.exceptions import ValidationError
from underwrite_core.canonical import digest_bytes

from underwrite.instrument.ingest import inspection
from underwrite.instrument.ingest.application import (
    DEFAULT_MAX_BYTES,
    DIAGNOSTICS,
    NEXT_ACTIONS,
    instrument_diagnostic,
)
from underwrite.instrument.ingest.formats import trusted_codecs
from underwrite.instrument.ingest.release_bindings import (
    PROFILES,
    binding_facts,
    usable_identity,
)
from underwrite.instrument.ingest.schema import SchemaConfigurationError, packaged_validator
from underwrite.instrument.ingest.transport import DecodeError, TransportError, decode_json

BATCH_MAX_BYTES = 16 * 1024 * 1024


LIMITATIONS = (
    "SOURCE_CLAIMS_UNVERIFIED",
    "RELEASE_AUTHENTICITY_UNVERIFIED",
    "RUN_INDEPENDENCE_UNVERIFIED",
    "CONTENT_ADEQUACY_NOT_ASSESSED",
    "MEASUREMENT_NOT_PERFORMED",
    "NO_DEPLOYMENT_DECISION",
)


Row = dict[str, object]


class ReleaseError(ValueError):
    """Adapter-owned safe failure, never arbitrary received exception text."""

    def __init__(self, code: str, reason: str, location: str, action: str) -> None:
        super().__init__(code)
        self.payload: Row = dict(
            schema="release_inspection_error.v1",
            code=code,
            reason=reason,
            location=location,
            next_action=action,
            retryable=False,
            exit_code=2,
        )


def release_error(error: Exception, location: str = "") -> ReleaseError:
    if isinstance(error, inspection.InspectionError):
        value = error.payload
        return ReleaseError(
            str(value["code"]),
            str(value["reason"]),
            location + str(value["location"]),
            str(value["next_action"]),
        )
    if isinstance(error, SchemaConfigurationError):
        return ReleaseError(
            "SCHEMA_CONFIGURATION_ERROR",
            "INVALID_INSPECTION_SCHEMA",
            "",
            NEXT_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
        )
    detail = instrument_diagnostic(error, "check")
    code, reason = str(detail["code"]), str(detail["reason"])
    action = "REDUCE_BATCH_SIZE" if reason == "BYTE_LIMIT_EXCEEDED" else "CORRECT_MANIFEST"
    if code == "SOURCE_READ_FAILED":
        action = NEXT_ACTIONS[code]
    if isinstance(error, DecodeError) and code != "INPUT_LIMIT_EXCEEDED":
        code = "INVALID_MANIFEST"
    return ReleaseError(code, reason, location, action)


def validate_schema(value: object, name: str) -> None:
    try:
        packaged_validator(name).validate(value)  # pyright: ignore[reportUnknownMemberType]
    except ValidationError as exc:
        raise ReleaseError("INVALID_MANIFEST", "INVALID_MANIFEST", "", "CORRECT_MANIFEST") from exc
    except Exception as exc:
        raise ReleaseError(
            "SCHEMA_CONFIGURATION_ERROR",
            "INVALID_INSPECTION_SCHEMA",
            "",
            NEXT_ACTIONS["SCHEMA_CONFIGURATION_ERROR"],
        ) from exc


def as_rows(value: object) -> list[Row]:
    return cast(list[Row], value)


def as_row(value: object) -> Row:
    return cast(Row, value)


def _identities(manifest: Row, evidence: list[Row]) -> None:
    identities = [("/subject", as_row(manifest["subject"]))] + [
        (f"/evidence/{index}/declared", as_row(entry.get("declared", {})))
        for index, entry in enumerate(evidence)
    ]
    for location, identity in identities:
        for field, item in identity.items():
            if not usable_identity(item):
                raise ReleaseError(
                    "INVALID_MANIFEST",
                    "INVALID_MANIFEST",
                    location + "/" + field,
                    "CORRECT_MANIFEST",
                )


def validate_release_case(raw: bytes) -> Row:
    """The complete input validation boundary, before any artifact is opened."""
    if len(raw) > inspection.MANIFEST_MAX_BYTES:
        raise ReleaseError("INPUT_LIMIT_EXCEEDED", "BYTE_LIMIT_EXCEEDED", "", "REDUCE_BATCH_SIZE")
    try:
        value = decode_json(raw, inspection.MANIFEST_MAX_DEPTH)
        validate_schema(value, "release_case.v1")
        manifest = as_row(value)
        evidence = as_rows(manifest["evidence"])
        for index, entry in enumerate(evidence):
            try:
                inspection.validate_artifact_case(entry["case"])
            except inspection.InspectionError as exc:
                raise release_error(exc, f"/evidence/{index}/case") from exc
        for index, entry in enumerate(evidence):
            try:
                inspection.case_source(as_row(entry["case"]))
            except tuple(DIAGNOSTICS) as exc:
                raise release_error(exc, f"/evidence/{index}/case/source") from exc
        _identities(manifest, evidence)
        for key in ("requirements", "evidence"):
            entries = as_rows(manifest[key])
            if len({entry["id"] for entry in entries}) != len(entries):
                raise ReleaseError(
                    "INVALID_MANIFEST", "INVALID_MANIFEST", "/" + key, "CORRECT_MANIFEST"
                )
        return manifest
    except tuple(DIAGNOSTICS) as exc:
        raise release_error(exc) from exc


def _diagnostic(code: str, location: str, action: str) -> Row:
    return dict(code=code, location=location, field=None, source_path=None, next_action=action)


def _sort(diagnostics: list[Row]) -> None:
    diagnostics.sort(
        key=lambda item: tuple(
            str(item[key] or "") for key in ("location", "field", "code", "source_path")
        )
    )


def source_selector(metadata: Row) -> tuple[str, str]:
    return str(metadata["format"]), str(metadata["format_version"])


def unsupported_profile(selector: tuple[str, str]) -> str | None:
    if selector not in trusted_codecs():
        return "UNSUPPORTED_FORMAT"
    return "UNSUPPORTED_LINKAGE_PROFILE" if selector not in PROFILES else None


def is_eligible(entry: Row) -> bool:
    return (
        not entry["diagnostics"]
        and as_row(entry["artifact"])["projection"] == "created"
        and entry["record_presence"] == "present"
        and all(
            as_row(binding)["state"] == "matched" for binding in as_row(entry["bindings"]).values()
        )
    )


def _evidence(
    parent: int, entry: Row, index: int, subject: Row, remaining: int, flags: tuple[int, int]
) -> tuple[Row, int]:
    location = f"/evidence/{index}"
    case = as_row(entry["case"])
    selector = source_selector(as_row(case["source"]))
    artifact: Row = dict(
        raw_hash=None, observation=None, expected_hash_check="not_checked", projection="not_created"
    )
    diagnostics: list[Row] = []
    projection = None
    unsupported = unsupported_profile(selector)
    if unsupported:
        diagnostics.append(
            _diagnostic(unsupported, location + "/case/source", "SELECT_SUPPORTED_PROFILE")
        )
    else:
        source = inspection.case_source(case)
        metadata = as_row(case["artifact"])
        allowance = min(DEFAULT_MAX_BYTES, remaining)
        try:
            raw = inspection.read_at(parent, str(metadata["path"]).split("/"), allowance, flags)
        except TransportError as exc:
            if exc.args == ("BYTE_LIMIT_EXCEEDED",):
                raise release_error(exc, location + "/case/artifact/path") from exc
            if exc.attempted_read:
                remaining -= allowance
            detail = instrument_diagnostic(exc, "check")
            diagnostics.append(
                _diagnostic(
                    str(detail["reason"]),
                    location + "/case/artifact/path",
                    NEXT_ACTIONS["SOURCE_READ_FAILED"],
                )
            )
        else:
            remaining -= len(raw)
            artifact, details, projection = inspection.inspect_bytes(
                raw, source, metadata.get("expected_hash")
            )
            diagnostics.extend(
                _diagnostic(
                    str(item["reason"]),
                    location + "/case" + str(item["location"]),
                    str(item["next_action"]),
                )
                for item in details
            )
    bindings, facts, presence = binding_facts(
        projection,
        selector,
        cast(dict[str, str], subject),
        cast(dict[str, str], entry.get("declared", {})),
        location,
    )
    diagnostics.extend(cast(list[Row], facts))
    return dict(
        id=entry["id"],
        requirement=entry["requirement"],
        location=location,
        artifact=artifact,
        record_presence=presence,
        bindings=bindings,
        diagnostics=diagnostics,
    ), remaining


def compose(parent: int, raw: bytes, manifest: Row, flags: tuple[int, int]) -> Row:
    remaining = BATCH_MAX_BYTES
    evidence: list[Row] = []
    requirements = as_rows(manifest["requirements"])
    by_id = {item["id"]: item for item in requirements}
    for index, entry in enumerate(as_rows(manifest["evidence"])):
        result, remaining = _evidence(
            parent, entry, index, as_row(manifest["subject"]), remaining, flags
        )
        diagnostics = as_rows(result["diagnostics"])
        requirement = by_id.get(entry["requirement"])
        if requirement is None:
            diagnostics.append(
                _diagnostic("UNRESOLVED_REQUIREMENT", str(result["location"]), "CORRECT_MANIFEST")
            )
        elif source_selector(requirement) != source_selector(
            as_row(as_row(entry["case"])["source"])
        ):
            diagnostics.append(
                _diagnostic("SELECTOR_MISMATCH", str(result["location"]), "SELECT_REQUIRED_PROFILE")
            )
        evidence.append(result)
    for entry in evidence:
        raw_hash = as_row(entry["artifact"])["raw_hash"]
        if (
            raw_hash is not None
            and sum(as_row(other["artifact"])["raw_hash"] == raw_hash for other in evidence) > 1
        ):
            as_rows(entry["diagnostics"]).append(
                _diagnostic(
                    "DUPLICATE_EVIDENCE", str(entry["location"]), "REMOVE_DUPLICATE_REFERENCE"
                )
            )
        _sort(as_rows(entry["diagnostics"]))
    outcomes: list[Row] = []
    for index, requirement in enumerate(requirements):
        candidates = [entry for entry in evidence if entry["requirement"] == requirement["id"]]
        diagnostics: list[Row] = []
        location = f"/requirements/{index}"
        unsupported = unsupported_profile(source_selector(requirement))
        if unsupported:
            diagnostics.append(_diagnostic(unsupported, location, "SELECT_SUPPORTED_PROFILE"))
        if not candidates:
            diagnostics.append(
                _diagnostic("MISSING_EVIDENCE", location, "PROVIDE_REQUIRED_EVIDENCE")
            )
        elif len(candidates) > 1:
            diagnostics.append(_diagnostic("AMBIGUOUS_EVIDENCE", location, "SELECT_ONE_EVIDENCE"))
        _sort(diagnostics)
        matched = not diagnostics and len(candidates) == 1 and is_eligible(candidates[0])
        outcomes.append(
            dict(
                id=requirement["id"],
                result="matched" if matched else "unresolved",
                evidence_ids=[entry["id"] for entry in candidates],
                diagnostics=diagnostics,
            )
        )
    matched = all(item["result"] == "matched" for item in outcomes) and all(
        entry["requirement"] in by_id for entry in evidence
    )
    return dict(
        schema="release_inspection.v1",
        manifest_hash=digest_bytes(raw),
        scope="declared_release_linkage",
        result="requirements_matched" if matched else "gaps_found",
        subject=manifest["subject"],
        requirements=outcomes,
        evidence=evidence,
        limitations=list(LIMITATIONS),
    )
